//! abandon-ship-server: pays expired, unrefreshed Ark coins on-chain to the
//! coin's own key. See README.md and docs/design.md.

mod chain;
mod checks;
mod config;
mod db;
mod journal;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::str::FromStr;
use std::time::Duration;

use ark::{ProtocolEncoding, Vtxo};
use bitcoin::secp256k1::Secp256k1;
use bitcoin::{Address, ScriptBuf, Txid};
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

#[tokio::main]
async fn main() -> anyhow::Result<()> {
	tracing_subscriber::fmt()
		.with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
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
	let chain = chain::Chain::new(&cfg.bitcoind.url, &cfg.bitcoind.user, &cfg.bitcoind.pass)?;
	let mut journal = journal::Journal::open(&cfg.journal_path)?;

	loop {
		// Infrastructure errors are retried next tick; only an invariant
		// violation stops the process.
		if let Err(e) = tick(&cfg, &sweep_spks, &mut db, &chain, &mut journal).await {
			if e.downcast_ref::<InvariantViolation>().is_some() { return Err(e) }
			warn!("tick failed, retrying: {e:#}");
		}
		db::check_invariants(&db).await?;
		if once { return Ok(()) }
		tokio::time::sleep(Duration::from_secs(cfg.poll_interval_secs)).await;
	}
}

/// One pass. Infrastructure errors (DB, bitcoind) abort the tick and are
/// retried next tick; problems with a single coin quarantine that coin only.
async fn tick(
	cfg: &Config, sweep_spks: &[ScriptBuf], db: &mut tokio_postgres::Client, chain: &chain::Chain,
	journal: &mut journal::Journal,
) -> anyhow::Result<()> {
	// Every tick: captaind may be upgraded under us.
	let ver = db::captaind_schema_version(db).await?;
	if !cfg.postgres.allowed_schema_versions.contains(&ver) {
		return Err(InvariantViolation(format!("captaind schema version {ver} not in allowed_schema_versions")).into());
	}
	chain.check_wallet().await?;
	let tip = chain.tip().await?;

	// Journal first: any ledger row with a txid must be journaled (closes the
	// crash window between storing a tx and journaling it), and any journaled
	// coin that is live again in captaind (DB restore) is re-marked spent
	// before a user can refresh it. Scans the whole journal every tick.
	for (id, txid, raw) in db::paid_ids(db).await? {
		if !journal.contains(&id) { journal.record(&[id], &txid, &raw)? }
	}
	for id in db::resurrected(db, &journal.ids()).await? {
		let flipped = db::reassert_paid(db, &id).await?;
		warn!(vtxo = %id, flipped, "journaled coin live again in captaind (restore?); re-marked spent");
		db::quarantine(db, &id, "already paid per local journal").await?;
		// The restored DB may have lost the tx before it was broadcast: send
		// the journaled one (a no-op if it is already known or mined).
		if let Some(raw) = journal.raw_tx(&id) {
			if let Err(e) = chain.broadcast(raw).await {
				warn!(vtxo = %id, "journaled payout tx not accepted: {e:#}");
			}
		}
	}

	settle_inflight(db, chain).await?;

	// Fee gate: bitcoind's real estimate only, no fallback rate, ever. No
	// estimate: no claims and no payouts this tick. There is no feerate cap:
	// the per-coin percentage rule bounds what any coin can lose to fees.
	let p = &cfg.policy;
	let Some(fee_rate) = chain.estimate_fee_rate(p.payout_conf_target).await? else {
		warn!("no fee estimate: not claiming or paying this tick");
		return Ok(());
	};

	// Never pile up claims. Pay what is claimed before claiming more.
	if !db::payouts_in_state(db, "claimed").await?.is_empty() {
		return pay_claimed(cfg, db, chain, journal, fee_rate).await;
	}
	let mut claims_left = p.max_batch;
	let mut quarantined: u64 = 0;
	for c in db::candidates(db, tip, p.grace_blocks, p.max_batch, p.min_payout_sat).await? {
		if claims_left == 0 { break }
		if journal.contains(&c.vtxo_id) { continue } // handled above
		match process_coin(cfg, sweep_spks, db, chain, tip, fee_rate, &c).await? {
			Outcome::Quarantine(reason) => {
				quarantined += 1;
				if quarantined > p.max_quarantine_per_tick {
					// Many failures at once smell like an encoding/schema change,
					// not bad coins: stop instead of quarantining everything.
					return Err(InvariantViolation(format!(
						"more than {} quarantines in one tick (last: {reason})", p.max_quarantine_per_tick)).into());
				}
				warn!(vtxo = %c.vtxo_id, %reason, "quarantined");
				db::quarantine(db, &c.vtxo_id, &reason).await?;
			},
			Outcome::Claimed => { claims_left -= 1; info!(vtxo = %c.vtxo_id, "claimed") },
			Outcome::Lost => info!(vtxo = %c.vtxo_id, "user redeemed first; skipped"),
			Outcome::Banned => info!(vtxo = %c.vtxo_id, "banned; waiting before claim"),
			Outcome::Wait(why) => tracing::debug!(vtxo = %c.vtxo_id, why, "waiting"),
		}
	}

	pay_claimed(cfg, db, chain, journal, fee_rate).await
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
	// The funding output must be spent, on-chain, by a sweep to our
	// scripts only, buried deep enough. The DB only tells us where to look.
	if chain.is_unspent(anchor).await? { return Ok(Outcome::Wait("anchor not swept yet")) }
	let Some(spender) = db::recorded_spender(db, &anchor.to_string()).await? else {
		return Ok(Outcome::Wait("no recorded spender for the anchor yet"));
	};
	let Ok(spender_txid) = Txid::from_str(&spender) else {
		return Ok(Outcome::Quarantine(format!("unparseable spender txid {spender:?}")));
	};
	let (spend_tx, confs) = match chain.tx(spender_txid).await {
		Ok(x) => x,
		Err(e) => {
			warn!(vtxo = %c.vtxo_id, %spender, "spender tx unavailable: {e:#}");
			return Ok(Outcome::Wait("unavailable tx (see warn)"));
		},
	};
	if !checks::is_sweep(&spend_tx, sweep_spks) {
		// A tree tx (partial unroll) is permanent: quarantine. Anything else is
		// most likely a wrong sweep_addresses config: wait, never quarantine
		// en masse because of a config mistake.
		if db::is_tree_tx(db, &spender).await? {
			return Ok(Outcome::Quarantine(format!("round partially unrolled by {spender}")));
		}
		warn!(vtxo = %c.vtxo_id, %spender, "anchor spender pays outside sweep_addresses; check config");
		return Ok(Outcome::Wait("spender pays outside sweep_addresses"));
	}
	if confs < p.sweep_min_confs { return Ok(Outcome::Wait("sweep not deep enough")) }

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

/// Batch all claimed coins into one tx: verify it, store it, then broadcast.
async fn pay_claimed(
	cfg: &Config, db: &mut tokio_postgres::Client, chain: &chain::Chain, journal: &mut journal::Journal,
	fee_rate: f64,
) -> anyhow::Result<()> {
	let claimed = db::payouts_in_state(db, "claimed").await?;
	if claimed.is_empty() { return Ok(()) }
	// A claimed row for a coin already in the journal means the ledger was
	// reset or restored: never pay twice.
	if let Some(p) = claimed.iter().find(|p| journal.contains(&p.vtxo_id)) {
		return Err(InvariantViolation(format!("claimed coin {} was already paid (journal)", p.vtxo_id)).into());
	}
	// Coins sent to the same Ark address share a key, hence a payout address:
	// one output per address, carrying the sum.
	let mut per_address: BTreeMap<String, u64> = BTreeMap::new();
	for p in &claimed {
		*per_address.entry(p.address.clone()).or_default() += p.amount_sat;
	}
	// Outputs not affordable at this rate stay claimed until fees fall.
	let share = checks::fee_share_bound(fee_rate);
	let pct = cfg.policy.max_fee_pct_per_payout;
	per_address.retain(|_, amt| checks::affordable(*amt, share, pct));
	if per_address.is_empty() {
		warn!("claimed payouts not affordable at {fee_rate:.2} sat/vB; waiting");
		return Ok(());
	}
	let ids: Vec<String> = claimed.iter().filter(|p| per_address.contains_key(&p.address))
		.map(|p| p.vtxo_id.clone()).collect();
	let mut expected = Vec::with_capacity(per_address.len());
	for (addr, amt) in &per_address {
		let spk = Address::from_str(addr)?.require_network(cfg.network)?.script_pubkey();
		expected.push((spk, *amt));
	}

	let built = chain.build_payout(per_address.into_iter().collect(), fee_rate).await?;
	// Refuse anything that is not exactly the intended payout.
	let change = match checks::verify_payout(&built.tx, &expected, built.fee_sat, pct) {
		Ok(c) => c,
		Err(e) => {
			warn!("payout deferred: {e:#}");
			return Ok(());
		},
	};
	if let Some(spk) = change {
		anyhow::ensure!(chain.is_mine(spk, cfg.network).await?, "payout change output is not ours");
	}

	let txid = built.tx.compute_txid().to_string();
	db::mark_signed(db, &ids, &txid, &built.raw).await?; // persisted before broadcast
	journal.record(&ids, &txid, &built.raw)?;            // and on local disk
	chain.broadcast(built.raw).await?;
	db::set_state_by_txid(db, &txid, "signed", "broadcast").await?;
	info!(%txid, coins = ids.len(), fee = built.fee_sat, "payout broadcast");
	Ok(())
}

/// Broadcast stored txs (crash recovery) and mark confirmed ones.
async fn settle_inflight(db: &mut tokio_postgres::Client, chain: &chain::Chain) -> anyhow::Result<()> {
	for (txid, raw) in db::txs_in_state(db, "signed").await? {
		chain.broadcast(raw).await?;
		db::set_state_by_txid(db, &txid, "signed", "broadcast").await?;
	}
	for (txid, raw) in db::txs_in_state(db, "broadcast").await? {
		let confs = chain.confirmations(&txid).await.unwrap_or(0);
		if confs >= 6 {
			db::set_state_by_txid(db, &txid, "broadcast", "confirmed").await?;
		} else if confs == 0 {
			// Evicted from mempools or never relayed: rebroadcast the
			// stored tx itself. Never build a new one for the same coins.
			if let Err(e) = chain.broadcast(raw).await {
				warn!(%txid, "rebroadcast failed: {e:#}");
			}
		}
	}
	Ok(())
}
