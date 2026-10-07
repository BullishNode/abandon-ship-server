//! abandon-ship-server: pays expired, unrefreshed Ark coins on-chain to the
//! coin's own key. See README.md and docs/design.md.

mod chain;
mod checks;
mod config;
mod db;
mod journal;

use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::str::FromStr;
use std::time::{Duration, Instant};

use ark::{ProtocolEncoding, Vtxo};
use bitcoin::secp256k1::Secp256k1;
use bitcoin::{Address, OutPoint, ScriptBuf, Txid};
use tracing::{info, warn};

use crate::config::Config;

/// A ledger inconsistency that must stop the process, not be retried.
#[derive(Debug)]
struct InvariantViolation(String);
impl std::fmt::Display for InvariantViolation {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { write!(f, "invariant violated: {}", self.0) }
}
impl std::error::Error for InvariantViolation {}

/// What happened to one candidate coin this tick.
enum Outcome {
	/// Not ready yet; the reason is logged at debug level.
	Wait(&'static str),
	Banned,
	Claimed,
	/// The user redeemed it first.
	Lost,
	/// Never touched again automatically.
	Quarantine(String),
}

#[derive(Default)]
struct TickStats {
	tip: Option<u32>,
	candidates: u64,
	claims: u64,
	payouts_broadcast: u64,
	quarantines: u64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
	tracing_subscriber::fmt()
		.with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
			.unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")))
		.init();

	let mut args = std::env::args().skip(1);
	let cfg_path = PathBuf::from(args.next().unwrap_or_else(|| "config.toml".into()));
	let once = args.any(|a| a == "--once");
	let cfg = Config::load(&cfg_path)?;
	let sweep_spks = cfg.policy.sweep_addresses.iter()
		.map(|a| Ok(Address::from_str(a)?.require_network(cfg.network)
			.map_err(|e| anyhow::anyhow!("sweep address {a} is not a {} address: {e}", cfg.network))?
			.script_pubkey()))
		.collect::<anyhow::Result<Vec<ScriptBuf>>>()?;

	let mut db = db::connect(&cfg.postgres.conninfo).await?;
	// Exactly one instance.
	anyhow::ensure!(db::try_lead(&db).await?, "another sidecar instance holds the leader lock");
	db::check_tables(&db).await?;
	// Earlier builds quarantined the whole round. Reconsider those coins
	// under the exact-path rule; every payout still requires chain evidence.
	db.execute("DELETE FROM sidecar.quarantine WHERE reason LIKE 'round partially unrolled by %'", &[]).await?;
	let chain = chain::Chain::new(&cfg.bitcoind.url, &cfg.bitcoind.user, &cfg.bitcoind.pass)?;
	let mut journal = journal::Journal::open(&cfg.journal_path)?;

	loop {
		// A one-shot recovery must report failure to its caller. The daemon
		// retries infrastructure errors; invariant violations stop either mode.
		let started = Instant::now();
		let mut stats = TickStats::default();
		let result = tick(&cfg, &sweep_spks, &mut db, &chain, &mut journal, &mut stats).await;
		let invariants = db::check_invariants(&db).await;
		info!(tip = ?stats.tip, candidates = stats.candidates, claims = stats.claims,
			payouts_broadcast = stats.payouts_broadcast, quarantines = stats.quarantines,
			duration_ms = started.elapsed().as_millis(), success = result.is_ok() && invariants.is_ok(), "tick summary");
		if let Err(e) = result {
			if once || e.downcast_ref::<InvariantViolation>().is_some() { return Err(e) }
			warn!("tick failed, retrying: {e:#}");
		}
		invariants?;
		if once { return Ok(()) }
		tokio::time::sleep(Duration::from_secs(cfg.poll_interval_secs)).await;
	}
}

/// One pass. Infrastructure errors (DB, bitcoind) abort the tick and are
/// retried next tick; problems with a single coin quarantine that coin only.
async fn tick(
	cfg: &Config, sweep_spks: &[ScriptBuf], db: &mut tokio_postgres::Client, chain: &chain::Chain,
	journal: &mut journal::Journal, stats: &mut TickStats,
) -> anyhow::Result<()> {
	// Every tick: captaind may be upgraded under us.
	let ver = db::captaind_schema_version(db).await?;
	if !cfg.postgres.allowed_schema_versions.contains(&ver) {
		return Err(InvariantViolation(format!("captaind schema version {ver} not in allowed_schema_versions")).into());
	}
	reconcile_journal(db, journal, stats).await?;
	chain.check_wallet().await?;
	let tip = chain.tip().await?;
	stats.tip = Some(tip);


	settle_inflight(db, chain, journal, stats).await?;

	// Fee gate: bitcoind's real estimate only, no fallback rate, ever. No
	// estimate: no new claims or transactions. Stored payouts were retried above.
	// There is no feerate cap:
	// the per-coin percentage rule bounds what any coin can lose to fees.
	let p = &cfg.policy;
	let Some(fee_rate) = chain.estimate_fee_rate(p.payout_conf_target).await? else {
		warn!("no fee estimate: not claiming or building payouts this tick");
		return Ok(());
	};

	// Pay existing claims first. An estimate alone must not let a claim that
	// fails the actual fee check occupy the batch forever.
	if pay_claimed(cfg, db, chain, journal, fee_rate, stats).await? { return Ok(()) }
	let share = checks::fee_share_bound(fee_rate);
	let mut claims_left = p.max_batch;
	let mut quarantined: u64 = 0;
	// Coins unaffordable at this rate are left out before the candidate
	// limit: waiting for fees to fall, they must not crowd out payable coins.
	let min_amount = p.min_payout_sat.max(checks::min_affordable(share, p.max_fee_pct_per_payout));
	for c in db::candidates(db, tip, p.grace_blocks, p.max_batch, min_amount).await? {
		if claims_left <= 0 { break }
		stats.candidates += 1;
		if journal.contains(&c.vtxo_id) { continue } // handled above
		let reason = match process_coin(cfg, sweep_spks, db, chain, tip, fee_rate, &c).await? {
			Outcome::Quarantine(reason) => reason,
			Outcome::Claimed => { stats.claims += 1; claims_left -= 1; info!(vtxo = %c.vtxo_id, "claimed"); continue },
			Outcome::Lost => { info!(vtxo = %c.vtxo_id, "user redeemed first; skipped"); continue },
			Outcome::Banned => { info!(vtxo = %c.vtxo_id, "banned; waiting before claim"); continue },
			Outcome::Wait(why) => { tracing::debug!(vtxo = %c.vtxo_id, why, "waiting"); continue },
		};
		quarantined += 1;
		if quarantined > p.max_quarantine_per_tick {
			return Err(InvariantViolation(format!(
				"more than {} quarantines in one tick (last: {reason})", p.max_quarantine_per_tick)).into());
		}
		warn!(vtxo = %c.vtxo_id, %reason, "quarantined");
		db::quarantine(db, &c.vtxo_id, &reason).await?;
		stats.quarantines += 1;
	}

	pay_claimed(cfg, db, chain, journal, fee_rate, stats).await?;
	Ok(())
}

/// Reassert settlement before any chain access. The startup gate observes
/// the marker only after the complete journal pass and ledger checks succeed.
async fn reconcile_journal(
	db: &mut tokio_postgres::Client, journal: &mut journal::Journal, stats: &mut TickStats,
) -> anyhow::Result<()> {
	// Journal first: any ledger row with a txid must be journaled (closes the
	// crash window between storing a tx and journaling it), and any journaled
	// coin that is live again in captaind (DB restore) is re-marked spent
	// before a user can refresh it. Scans the whole journal every tick.
	let mut unjournaled: BTreeMap<String, Vec<String>> = BTreeMap::new();
	let mut pending_txids = HashSet::new();
	for (id, txid, confirmed) in db::paid_ids(db).await? {
		if confirmed { journal.mark_confirmed(&txid); }
		else { pending_txids.insert(txid.clone()); }
		if !journal.contains(&id) || !journal.has_transaction(&txid) {
			unjournaled.entry(txid).or_default().push(id);
		}
	}
	for (txid, ids) in unjournaled {
		journal.record(&ids, &txid, &db::raw_tx(db, &txid).await?)?;
	}
	db::check_journal_history(db, &journal.ids()).await
		.map_err(|e| InvariantViolation(e.to_string()))?;
	journal.check_transactions().map_err(|e| InvariantViolation(e.to_string()))?;
	for id in db::resurrected(db, &journal.ids()).await? {
		let flipped = db::reassert_paid(db, &id).await?;
		warn!(vtxo = %id, flipped, "journaled coin live again in captaind (restore?); re-marked spent");
		db::quarantine(db, &id, "payout committed in local journal").await?;
		stats.quarantines += 1;
	}
	// A backup taken after a claim but before signing still has its row.
	// Reattach its journaled transaction instead of building another payment.
	let mut restored: BTreeMap<String, Vec<String>> = BTreeMap::new();
	for claim in db::claimed_payouts(db).await? {
		if let Some(txid) = journal.txid(&claim.vtxo_id) {
			restored.entry(txid.to_owned()).or_default().push(claim.vtxo_id);
		}
	}
	for (txid, ids) in restored {
		let raw = journal.raw_tx(&ids[0]).ok_or_else(|| InvariantViolation(format!(
			"journaled coins {ids:?}, payout {txid}: unreadable raw transaction")))?;
		db::mark_signed(db, &ids, &txid, &raw).await?;
		pending_txids.insert(txid);
	}
	// One confirmed row must not hide another restored member of its batch.
	for txid in pending_txids { journal.mark_pending(&txid); }

	db::check_invariants(db).await?;
	db.execute("INSERT INTO sidecar.reassert (id, completed_at) VALUES (1, clock_timestamp())
		ON CONFLICT (id) DO UPDATE SET completed_at = EXCLUDED.completed_at", &[]).await?;
	Ok(())
}

async fn process_coin(
	cfg: &Config, sweep_spks: &[ScriptBuf], db: &mut tokio_postgres::Client, chain: &chain::Chain,
	tip: u32, fee_rate: f64, c: &db::Candidate,
) -> anyhow::Result<Outcome> {
	let p = &cfg.policy;

	// Amount, key and anchor come from the stored VTXO; the DB is trusted.
	let vtxo: Vtxo = match Vtxo::deserialize(&c.vtxo) {
		Ok(v) => v,
		Err(e) => return Ok(Outcome::Quarantine(format!("undecodable vtxo: {e}"))),
	};
	let anchor = vtxo.chain_anchor();
	let amount = vtxo.amount().to_sat();
	// Not worth paying on-chain at today's fee: leave it alone (no ban, no
	// claim), so its owner can still refresh it. Re-checked every tick.
	if !checks::affordable(amount, checks::fee_share_bound(fee_rate), p.max_fee_pct_per_payout) {
		return Ok(Outcome::Wait("fee share above max_fee_pct_per_payout"));
	}
	let mut predecessors = Vec::new();
	if c.unclaimed {
		let Some(unlock_hash) = vtxo.unlock_hash() else {
			return Ok(Outcome::Quarantine("unclaimed output has no hArk participation hash".into()));
		};
		let inputs = db::unclaimed_inputs(db, &anchor.txid.to_string(), &unlock_hash.to_string()).await?;
		if inputs.is_empty() || inputs.iter().any(Option::is_none) {
			return Ok(Outcome::Quarantine("unclaimed output's original input history is missing".into()));
		}
		for raw in inputs.into_iter().flatten() {
			match Vtxo::deserialize(&raw) {
				Ok(input) => predecessors.push(input),
				Err(e) => return Ok(Outcome::Quarantine(format!("undecodable unclaimed input: {e}"))),
			}
		}
	}
	// A replacement funded before forfeits finish does not cancel its old
	// inputs' exit transactions. Each must be swept on its own exact path too.
	for (original_input, proof) in std::iter::once((false, &vtxo))
		.chain(predecessors.iter().map(|input| (true, input))) {
		let anchor = proof.chain_anchor();
		if chain.is_unspent(anchor).await? { return Ok(Outcome::Wait("anchor not swept yet")) }
		let (anchor_tx, _) = chain.tx(anchor.txid).await?;
		if let Err(e) = proof.validate_unsigned(&anchor_tx) {
			return Ok(Outcome::Quarantine(format!("invalid exit path: {e}")));
		}
		// Only this coin's output at each level belongs to its exit path. A
		// swept sibling, even in the same transaction, cannot settle this coin.
		let path = std::iter::once(anchor).chain(proof.transactions()
			.map(|t| OutPoint::new(t.tx.compute_txid(), t.output_idx as u32)));
		let mut swept = false;
		for outpoint in path {
			if outpoint != anchor && chain.is_unspent(outpoint).await? { break; }
			let Some(spender) = db::recorded_spender(db, &outpoint.to_string()).await? else { continue; };
			let Ok(spender_txid) = Txid::from_str(&spender) else {
				return Ok(Outcome::Quarantine(format!("unparseable spender txid {spender:?}")));
			};
			let (spend_tx, confs) = match chain.tx(spender_txid).await {
				Ok(x) => x,
				Err(e) => {
					warn!(vtxo = %proof.id(), %spender, "spender tx unavailable: {e:#}");
					continue;
				},
			};
			if checks::is_sweep_of(&spend_tx, outpoint, sweep_spks) {
				if confs < p.sweep_min_confs { return Ok(Outcome::Wait("sweep not deep enough")) }
				swept = true;
				break;
			}
		}
		if !swept {
			return Ok(Outcome::Wait(if original_input {
				"unclaimed replacement's original input has no confirmed sweep"
			} else { "no confirmed sweep on this coin's exit path" }));
		}
	}

	// Nothing in flight may hold the coin.
	if db::in_round_participation(db, &c.vtxo_id).await? { return Ok(Outcome::Wait("in a round participation")) }

	// Ban via captaind's own column, then wait (liveness only).
	match db::ban_age_secs(db, &c.vtxo_id, tip).await? {
		None => {
			let until = tip.checked_add(p.ban_blocks).ok_or_else(|| anyhow::anyhow!("ban overflow"))?;
			db::ban(db, &c.vtxo_id, until).await?;
			return Ok(Outcome::Banned);
		},
		Some(age) if age < p.ban_wait_secs as f64 => return Ok(Outcome::Wait("ban wait")),
		Some(_) => {},
	}
	if db::in_round_participation(db, &c.vtxo_id).await? { return Ok(Outcome::Wait("in a round participation")) }

	// The atomic claim. The payout goes to BIP86 tr(coin key), key path only.
	let key = vtxo.user_pubkey().x_only_public_key().0;
	let address = Address::p2tr(&Secp256k1::verification_only(), key, None, cfg.network).to_string();
	Ok(if db::claim(db, &c.vtxo_id, &anchor.to_string(), amount, &address, tip).await? {
		Outcome::Claimed
	} else {
		Outcome::Lost
	})
}

/// Pay one affordable batch, leaving deferred claims for a later tick.
async fn pay_claimed(
	cfg: &Config, db: &mut tokio_postgres::Client, chain: &chain::Chain, journal: &mut journal::Journal,
	fee_rate: f64, stats: &mut TickStats,
) -> anyhow::Result<bool> {
	let claimed = db::claimed_payouts(db).await?;
	// A claimed row for a coin already in the journal means the ledger was
	// reset or restored: never pay twice.
	if let Some(p) = claimed.iter().find(|p| journal.contains(&p.vtxo_id)) {
		return Err(InvariantViolation(format!("claimed coin {} was already paid (journal)", p.vtxo_id)).into());
	}
	let share = checks::fee_share_bound(fee_rate);
	let pct = cfg.policy.max_fee_pct_per_payout;
	for batch in claimed.chunks(cfg.policy.max_batch as usize) {
		// Coins that share a payout address share one output.
		let mut per_address: BTreeMap<String, u64> = BTreeMap::new();
		for p in batch {
			*per_address.entry(p.address.clone()).or_default() += p.amount_sat;
		}
		per_address.retain(|_, amt| checks::affordable(*amt, share, pct));
		while !per_address.is_empty() {
			let mut expected = Vec::with_capacity(per_address.len());
			for (addr, amt) in &per_address {
				let spk = Address::from_str(addr)?.require_network(cfg.network)?.script_pubkey();
				expected.push((spk, *amt));
			}

			let built = match chain.build_payout(per_address.clone().into_iter().collect(), fee_rate).await {
				Ok(built) => built,
				Err(e) => {
					warn!("payout funding deferred: {e:#}");
					if per_address.len() > 1 && (e.to_string().contains("Insufficient funds")
						|| e.to_string().contains("maximum weight")) {
						let largest = per_address.iter().max_by_key(|(_, amount)| *amount).unwrap().0.clone();
						per_address.remove(&largest);
						continue;
					}
					break;
				},
			};
			// Remove one rejected output at a time. A large output may require
			// many small inputs, making even a smaller, individually payable
			// output fail when both share that batch's fee.
			let mut rejected: Option<(String, u64)> = None;
			for ((addr, amount), (spk, _)) in per_address.iter().zip(&expected) {
				if let Some(output) = built.tx.output.iter().find(|o| &o.script_pubkey == spk) {
					let value = output.value.to_sat();
					if value <= *amount && !checks::affordable(*amount, amount - value, pct)
						&& rejected.as_ref().is_none_or(|(_, largest)| amount > largest) {
						rejected = Some((addr.clone(), *amount));
					}
				}
			}
			if let Some((addr, _)) = rejected {
				per_address.remove(&addr);
				warn!("payout deferred for an output above the actual fee bound");
				continue;
			}
			// Refuse anything that is not exactly the intended payout.
			let change = match checks::verify_payout(&built.tx, &expected, built.fee_sat, pct) {
				Ok(c) => c,
				Err(e) => {
					warn!("payout deferred: {e:#}");
					break;
				},
			};
			if let Some(spk) = change {
				anyhow::ensure!(chain.is_mine(spk, cfg.network).await?, "payout change output is not ours");
			}

			let txid = built.tx.compute_txid().to_string();
			let ids: Vec<String> = batch.iter().filter(|p| per_address.contains_key(&p.address))
				.map(|p| p.vtxo_id.clone()).collect();
			db::mark_signed(db, &ids, &txid, &built.raw).await?; // persisted before broadcast
			journal.record(&ids, &txid, &built.raw)?;            // and on local disk
			chain.broadcast(built.raw).await?;
			stats.payouts_broadcast += 1;
			db::set_state_by_txid(db, &txid, "broadcast").await?;
			info!(%txid, coins = ids.len(), fee = built.fee_sat, "payout broadcast");
			return Ok(true);
		}
	}
	Ok(false)
}

/// Retry every journaled transaction until confirmed, including lost DB rows.
async fn settle_inflight(
	db: &mut tokio_postgres::Client, chain: &chain::Chain, journal: &mut journal::Journal, stats: &mut TickStats,
) -> anyhow::Result<()> {
	for (txid, raw) in journal.pending_transactions()? {
		let confs = chain.confirmations(&txid).await.unwrap_or(0);
		if confs >= 6 {
			db::set_state_by_txid(db, &txid, "confirmed").await?;
			journal.mark_confirmed(&txid);
		} else {
			if confs == 0 {
				if let Err(e) = chain.broadcast(raw).await {
					warn!(%txid, "stored payout not accepted; retrying next tick: {e:#}");
					continue;
				}
				stats.payouts_broadcast += 1;
			}
			db::set_state_by_txid(db, &txid, "broadcast").await?;
		}
	}
	// A rebroadcast parent may have made its change available this tick.
	// Reserve it for any still-pending child before building new payouts.
	for (_, raw) in journal.pending_transactions()? {
		chain.reserve_inputs(&raw).await?;
	}

	Ok(())
}
