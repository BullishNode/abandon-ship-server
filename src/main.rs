//! abandon-ship-server: pays expired, unrefreshed Ark coins on-chain to the
//! coin's own key. See README.md and docs/threat-model.md.

mod chain;
mod checks;
mod config;
mod db;
mod journal;
mod payout;

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::str::FromStr;
use std::time::Duration;

use ark::{ProtocolEncoding, Vtxo};
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

/// Decode a stored coin and validate it against the chain.
///
/// Fully signed coins must pass `Vtxo::validate`. An unclaimed hArk output
/// (a delegated refresh whose owner never came back) is stored without
/// its unlock preimage (captaind keeps it in `round_participation`) and its
/// final leaf transition is unsigned until the owner returns. Such a coin is
/// accepted only if (`unclaimed_ok`):
/// - full validation fails at the **last** transition only, a hash-locked
///   (v1) step, with "missing signature" or "missing preimage" (validation
///   is sequential, so every earlier step passed, signatures included);
/// - structure-only validation of the whole chain passes;
/// - the output the last step spends (created by the signed parent) is
///   exactly the hArk leaf script for the coin's key: so the payout key is
///   committed by signed data and the unsigned step cannot redirect it;
/// - the coin's amount does not exceed that signed output's value.
async fn load_validated(
	db: &tokio_postgres::Client, chain: &chain::Chain, blob: &[u8],
) -> anyhow::Result<Result<(Vtxo, bitcoin::Transaction), String>> {
	let mut vtxo = match Vtxo::deserialize(blob) {
		Ok(v) => v,
		Err(e) => return Ok(Err(format!("undecodable vtxo: {e}"))),
	};
	if let Some(h) = vtxo.unlock_hash() {
		if let Some(pre) = db::unlock_preimage(db, &h.to_string()).await? {
			if let Ok(pre) = <[u8; 32]>::try_from(pre.as_slice()) {
				vtxo.provide_unlock_preimage(pre);
			}
		}
	}
	let anchor = vtxo.chain_anchor();
	let anchor_tx = match chain.tx(anchor.txid).await {
		Ok((tx, _)) => tx,
		Err(e) => return Err(e.context(format!("anchor tx {anchor} unavailable"))),
	};
	match vtxo.validate(&anchor_tx) {
		Ok(()) => Ok(Ok((vtxo, anchor_tx))),
		Err(e) => match unclaimed_ok(&vtxo, &anchor_tx, &e) {
			Ok(()) => Ok(Ok((vtxo, anchor_tx))),
			Err(why) => Ok(Err(format!("vtxo failed validation: {e} ({why})"))),
		},
	}
}

/// A validated coin must be a plain key coin of *our* server.
fn foreign(vtxo: &Vtxo, cfg: &Config) -> Option<&'static str> {
	if vtxo.server_pubkey() != cfg.server_pubkey { return Some("coin of another server") }
	if !matches!(vtxo.policy(), ark::VtxoPolicy::Pubkey(_)) { return Some("not a plain pubkey coin") }
	None
}

/// See `load_validated`: the one accepted partial-validation case.
fn unclaimed_ok(
	vtxo: &Vtxo, anchor_tx: &bitcoin::Transaction, err: &ark::vtxo::VtxoValidationError,
) -> Result<(), &'static str> {
	use ark::vtxo::VtxoValidationError as E;
	match err {
		E::GenesisTransition { error, genesis_idx, genesis_len, transition_kind }
			if *genesis_idx + 1 == *genesis_len
				&& *transition_kind == "hash-locked-cosigned-v1"
				&& (*error == "missing signature" || *error == "missing preimage") => {},
		_ => return Err("not an unsigned final hArk step"),
	}
	vtxo.validate_unsigned(anchor_tx).map_err(|_| "structure invalid")?;
	let unlock_hash = vtxo.unlock_hash().ok_or("no unlock hash")?;

	// The output the last tx spends, from the previous tx or the anchor.
	let txs: Vec<_> = vtxo.transactions().map(|i| i.tx).collect();
	let last = txs.last().ok_or("no genesis tx")?;
	let prev_out = last.input.first().ok_or("last tx has no input")?.previous_output;
	let prev_tx = if txs.len() >= 2 { &txs[txs.len() - 2] } else { anchor_tx };
	if prev_tx.compute_txid() != prev_out.txid { return Err("last tx does not spend the previous step") }
	let prev_txout = prev_tx.output.get(prev_out.vout as usize).ok_or("prev vout missing")?;

	let leaf = ark::vtxo::policy::HarkLeafVtxoPolicy { user_pubkey: vtxo.user_pubkey(), unlock_hash };
	let spend = leaf.taproot(vtxo.server_pubkey(), vtxo.expiry_height());
	let expected = ScriptBuf::new_p2tr_tweaked(spend.output_key());
	if prev_txout.script_pubkey != expected { return Err("coin key not committed by the signed parent") }
	if vtxo.amount() > prev_txout.value { return Err("amount above the signed parent output") }
	Ok(())
}

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
	// Refuse unknown captaind schema versions.
	let ver = db::captaind_schema_version(&db).await?;
	anyhow::ensure!(cfg.postgres.allowed_schema_versions.contains(&ver),
		"captaind schema version {ver} not in allowed_schema_versions; run the upgrade gate first");
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
	// Every tick, not just at startup: captaind may be upgraded under us.
	let ver = db::captaind_schema_version(db).await?;
	if !cfg.postgres.allowed_schema_versions.contains(&ver) {
		return Err(InvariantViolation(format!("captaind schema version changed to {ver}")).into());
	}
	chain.check_wallet().await?;
	let tip = chain.tip().await?;

	// Journal first: any ledger row with a txid must be journaled (closes the
	// crash window between storing a tx and journaling it), and any journaled
	// coin that is live again in captaind (DB restore) is re-marked spent
	// before a user can refresh it. Scans the whole journal every tick.
	let missing: Vec<(String, String)> = db::paid_ids(db).await?
		.into_iter().filter(|(id, _)| !journal.contains(id)).collect();
	for (id, txid) in missing {
		journal.record(&[id], &txid)?;
	}
	for id in db::resurrected(db, &journal.ids()).await? {
		let flipped = db::reassert_paid(db, &id).await?;
		warn!(vtxo = %id, flipped, "journaled coin live again in captaind (restore?); re-marked spent");
		db::quarantine(db, &id, "already paid per local journal").await?;
	}

	settle_inflight(db, chain).await?;

	// Fee gate (claims only). A claimed coin can no longer be refreshed by its
	// owner, so never claim unless we can pay right now: a usable estimate
	// within the cap. Without it, already-claimed coins still get paid.
	let p = &cfg.policy;
	// bitcoind's real estimate only; no fallback rate, ever.
	let fee_rate = chain.estimate_fee_rate(p.payout_conf_target).await?;
	// No estimate: no claims and no payouts this tick. There is no feerate
	// cap: the per-coin percentage rule bounds what any coin can lose to fees,
	// even if the estimate is absurd.
	let Some(fee_rate) = fee_rate else {
		warn!("no fee estimate: not claiming or paying this tick");
		return Ok(());
	};

	// Never pile up claims. Pay what is claimed before claiming more.
	if db::count_in_state(db, "claimed").await? > 0 {
		return pay_claimed(cfg, db, chain, journal, fee_rate).await;
	}
	let mut claims_left = p.max_batch;
	let mut quarantined: u64 = 0;
	for c in db::candidates(db, tip, p.grace_blocks, p.max_batch).await? {
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

	// Skip uneconomic coins before any work (the DB amount only skips; the
	// validated amount decides payment below).
	if c.db_amount < p.min_payout_sat
		|| !checks::affordable(c.db_amount, checks::fee_share_bound(fee_rate), p.max_fee_pct_per_payout) {
		return Ok(Outcome::Wait("below min_payout_sat or fee share above max_fee_pct_per_payout"));
	}

	// Decode and validate the coin against the chain; trust nothing else.
	let (vtxo, anchor_tx) = match load_validated(db, chain, &c.vtxo).await {
		Ok(Ok(x)) => x,
		Ok(Err(reason)) => return Ok(Outcome::Quarantine(reason)),
		// A coin whose anchor cannot be fetched must not block other coins.
		Err(e) => {
			warn!(vtxo = %c.vtxo_id, "{e:#}");
			return Ok(Outcome::Wait("anchor tx unavailable"));
		},
	};
	if vtxo.id().to_string() != c.vtxo_id {
		return Ok(Outcome::Quarantine("vtxo blob id != row id".into()));
	}
	if let Some(why) = foreign(&vtxo, cfg) {
		return Ok(Outcome::Quarantine(why.into()));
	}
	let anchor = vtxo.chain_anchor();
	if vtxo.expiry_height().to_u32().saturating_add(p.grace_blocks) > tip {
		return Ok(Outcome::Wait("not expired + grace per the validated VTXO"));
	}
	let amount = vtxo.amount().to_sat();
	// Not worth paying on-chain at today's fee: leave it alone (no ban, no
	// claim), so its owner can still refresh it. Re-checked every tick.
	if amount < p.min_payout_sat
		|| !checks::affordable(amount, checks::fee_share_bound(fee_rate), p.max_fee_pct_per_payout) {
		return Ok(Outcome::Wait("below min_payout_sat or fee share above max_fee_pct_per_payout"));
	}
	let Some(funding_out) = anchor_tx.output.get(anchor.vout as usize) else {
		return Ok(Outcome::Quarantine("anchor vout missing".into()));
	};

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
	if !spend_tx.input.iter().any(|i| i.previous_output == anchor) {
		return Ok(Outcome::Quarantine(format!("recorded spender {spender} does not spend the anchor")));
	}
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

	// The atomic claim, with I1 and the amount cross-check inside it.
	let address = payout::address_for_pubkey(&vtxo.user_pubkey(), cfg.network).to_string();
	Ok(match db::claim(db, &c.vtxo_id, &anchor.to_string(), amount, funding_out.value.to_sat(), &address, tip).await? {
		db::Claim::Claimed => Outcome::Claimed,
		db::Claim::Lost => Outcome::Lost,
		db::Claim::Refused(reason) => Outcome::Quarantine(reason),
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
	// At pay time: the ledger row is in a DB others can
	// write. Re-derive amount and address from the chain-validated VTXO; any
	// mismatch stops the sidecar instead of paying.
	for p in &claimed {
		let blob = db::vtxo_blob(db, &p.vtxo_id).await?
			.ok_or_else(|| anyhow::anyhow!("claimed coin {} has no vtxo row", p.vtxo_id))?;
		let (vtxo, _) = load_validated(db, chain, &blob).await?
			.map_err(|r| InvariantViolation(format!("claimed coin {}: {r}", p.vtxo_id)))?;
		if let Some(why) = foreign(&vtxo, cfg) {
			return Err(InvariantViolation(format!("claimed coin {}: {why}", p.vtxo_id)).into());
		}
		let addr = payout::address_for_pubkey(&vtxo.user_pubkey(), cfg.network).to_string();
		if vtxo.id().to_string() != p.vtxo_id || vtxo.amount().to_sat() != p.amount_sat || addr != p.address {
			return Err(InvariantViolation(format!("payout row {} differs from its validated VTXO", p.vtxo_id)).into());
		}
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
	let paying: std::collections::BTreeSet<&String> = per_address.keys().collect();
	let ids: Vec<String> = claimed.iter().filter(|p| paying.contains(&p.address))
		.map(|p| p.vtxo_id.clone()).collect();
	let mut expected = Vec::with_capacity(per_address.len());
	for (addr, amt) in &per_address {
		let spk = Address::from_str(addr)?.require_network(cfg.network)?.script_pubkey();
		expected.push((spk, *amt));
	}
	let outputs: Vec<(String, u64)> = per_address.clone().into_iter().collect();

	let built = chain.build_payout(outputs, fee_rate).await?;
	// Refuse anything that is not exactly the intended payout.
	let change = match checks::verify_payout(
		&built.tx, &expected, built.fee_sat, pct,
	) {
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
	journal.record(&ids, &txid)?;                        // and on local disk
	chain.broadcast(built.raw).await?;
	db::set_state_by_txid(db, &txid, "signed", "broadcast").await?;
	info!(%txid, coins = ids.len(), fee = built.fee_sat, "payout broadcast");
	Ok(())
}

/// Rebroadcast stored txs (crash recovery) and mark confirmed ones.
/// TODO: fee bump by RBF spending the same inputs.
async fn settle_inflight(db: &mut tokio_postgres::Client, chain: &chain::Chain) -> anyhow::Result<()> {
	let mut done = BTreeSet::new();
	for p in db::payouts_in_state(db, "signed").await? {
		if let (Some(t), Some(r)) = (p.txid, p.raw_tx) {
			if done.insert(t.clone()) {
				chain.broadcast(r).await?;
				db::set_state_by_txid(db, &t, "signed", "broadcast").await?;
			}
		}
	}
	let sent: BTreeSet<String> = db::payouts_in_state(db, "broadcast").await?
		.into_iter().filter_map(|p| p.txid).collect();
	for txid in sent {
		let confs = chain.confirmations(&txid).await.unwrap_or(0);
		if confs >= 6 {
			db::set_state_by_txid(db, &txid, "broadcast", "confirmed").await?;
		} else if confs == 0 {
			// Evicted from mempools or never relayed: rebroadcast the
			// stored tx itself. Never build a new one for the same coins.
			if let Some(raw) = db::raw_tx_by_txid(db, &txid).await? {
				if let Err(e) = chain.broadcast(raw).await {
					warn!(%txid, "rebroadcast failed: {e:#}");
				}
			}
		}
	}
	Ok(())
}
