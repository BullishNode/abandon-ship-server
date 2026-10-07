//! abandon-ship-server: pays expired, unrefreshed Ark coins on-chain to the
//! coin's own key. See README.md and docs/design.md.

mod captaind;
mod chain;
mod checks;
mod config;
mod db;
mod journal;
mod receipt;

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::io::Write;
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
	let mut once = false;
	let mut export = None;
	let mut receipts_only = false;
	while let Some(arg) = args.next() {
		match arg.as_str() {
			"--once" => once = true,
			"--export-receipts" => receipts_only = true,
			"--export-settlement-ids" => export = Some(PathBuf::from(args.next()
				.ok_or_else(|| anyhow::anyhow!("missing settlement ID output path"))?)),
			_ => anyhow::bail!("unknown argument {arg}"),
		}
	}
	anyhow::ensure!(!receipts_only || export.is_none(), "choose one export mode");
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
	if receipts_only {
		let chain = chain::Chain::new(&cfg.bitcoind.url, &cfg.bitcoind.user, &cfg.bitcoind.pass)?;
		let journal = journal::Journal::open(&cfg.journal_path)?;
		let txids = db::paid_ids(&db).await?.into_iter().map(|(_, txid, _)| txid)
			.chain(journal.txids()).collect::<BTreeSet<_>>();
		let mut failed = 0;
		for txid in txids {
			if let Err(e) = ensure_receipt(&cfg, &db, &chain, &txid).await {
				warn!(%txid, "receipt export failed: {e:#}");
				failed += 1;
			}
		}
		anyhow::ensure!(failed == 0, "{failed} payout receipts could not be exported");
		return Ok(());
	}
	// Earlier builds quarantined the whole round. Reconsider those coins
	// under the exact-path rule; every payout still requires chain evidence.
	db.execute("DELETE FROM sidecar.quarantine WHERE reason LIKE 'round partially unrolled by %'", &[]).await?;
	let mut journal = journal::Journal::open(&cfg.journal_path)?;
	if let Some(path) = export {
		reconcile_local(&mut db, &mut journal).await?;
		let ids = db::payout_ids(&db).await?.into_iter().chain(journal.ids())
			.collect::<BTreeSet<_>>();
		let temp = path.with_extension("tmp");
		let mut file = std::fs::File::create(&temp)?;
		for id in &ids { writeln!(file, "{id}")?; }
		file.sync_all()?;
		std::fs::rename(&temp, &path)?;
		let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(std::path::Path::new("."));
		std::fs::File::open(parent)?.sync_all()?;
		info!(ids = ids.len(), path = %path.display(), "exported settlement IDs; keep writers stopped until captaind replays them");
		return Ok(());
	}
	let chain = chain::Chain::new(&cfg.bitcoind.url, &cfg.bitcoind.user, &cfg.bitcoind.pass)?;
	let mut captaind = captaind::Captaind::new(&cfg.captaind_url)?;

	loop {
		// Infrastructure errors are retried next tick; only an invariant
		// violation stops the process.
		let started = Instant::now();
		let mut stats = TickStats::default();
		let result = tick(&cfg, &sweep_spks, &mut db, &chain, &mut journal, &mut captaind, &mut stats).await;
		info!(tip = ?stats.tip, candidates = stats.candidates, claims = stats.claims,
			payouts_broadcast = stats.payouts_broadcast, quarantines = stats.quarantines,
			duration_ms = started.elapsed().as_millis(), success = result.is_ok(), "tick summary");
		if let Err(e) = result {
			if e.downcast_ref::<InvariantViolation>().is_some() { return Err(e) }
			warn!("tick failed, retrying: {e:#}");
		}
		if once { return Ok(()) }
		tokio::time::sleep(Duration::from_secs(cfg.poll_interval_secs)).await;
	}
}

/// Close the local signed-before-journal window and reattach journaled batches.
async fn reconcile_local(
	db: &mut tokio_postgres::Client, journal: &mut journal::Journal,
) -> anyhow::Result<()> {
	let mut unjournaled: BTreeMap<String, Vec<String>> = BTreeMap::new();
	let mut confirmed_ids = HashSet::new();
	for (id, txid, confirmed) in db::paid_ids(db).await? {
		if !journal.contains(&id) || !journal.has_transaction(&txid) {
			unjournaled.entry(txid).or_default().push(id.clone());
		}
		if confirmed { confirmed_ids.insert(id); }
	}
	for (txid, ids) in unjournaled {
		let raw = db::raw_tx(db, &txid).await?.ok_or_else(|| InvariantViolation(format!(
			"coins {ids:?}, payout {txid}: raw transaction missing from database and journal")))?;
		journal.record(&ids, &txid, &raw)?;
	}
	journal.reconcile_confirmed(&confirmed_ids);
	// A backup taken after a claim but before signing still has its row.
	// Reattach its journaled transaction instead of building another payment.
	let mut restored: BTreeMap<String, Vec<String>> = BTreeMap::new();
	for claim in db::claimed_payouts(db).await? {
		if let Some(txid) = journal.txid(&claim.vtxo_id) {
			restored.entry(txid.to_owned()).or_default().push(claim.vtxo_id);
		}
	}
	for (txid, ids) in restored {
		let raw = if let Some(raw) = journal.raw_tx(&ids[0]) { raw } else {
			let raw = db::raw_tx(db, &txid).await?.ok_or_else(|| InvariantViolation(format!(
				"coins {ids:?}, payout {txid}: raw transaction missing or unreadable in database and journal")))?;
			journal.record(&ids, &txid, &raw)?;
			raw
		};
		db::mark_signed(db, &ids, &txid, &raw).await?;
	}
	Ok(())
}

async fn store_claim(
	cfg: &Config, db: &tokio_postgres::Client, claim: &captaind::Candidate,
) -> anyhow::Result<()> {
	let vtxo: Vtxo = Vtxo::deserialize(&claim.vtxo)?;
	let key = vtxo.user_pubkey().x_only_public_key().0;
	let address = Address::p2tr(&Secp256k1::verification_only(), key, None, cfg.network).to_string();
	db::store_claim(db, &claim.vtxo_id, &vtxo.chain_anchor().to_string(), vtxo.amount().to_sat(), &address).await
}

/// Receipts are permanent; restart each scan so an older delayed claim cannot
/// commit behind a persisted cursor. Unknown receipts become local obligations.
async fn reconcile_claims(
	cfg: &Config, db: &tokio_postgres::Client, journal: &journal::Journal,
	captaind: &mut captaind::Captaind,
) -> anyhow::Result<()> {
	let known = db::payout_ids(db).await?;
	let mut missing = known.iter().cloned().chain(journal.ids()).collect::<BTreeSet<_>>();
	let mut cursor = (0, String::new());
	loop {
		let page = captaind.page(true, 0, 0, &cursor, 256).await?;
		let Some(last) = page.last() else { break; };
		cursor = (last.expiry, last.vtxo_id.clone());
		for receipt in page {
			missing.remove(&receipt.vtxo_id);
			if !known.contains(&receipt.vtxo_id) {
				let (status, claim) = captaind.claim(&receipt.vtxo_id, cfg.policy.grace_blocks).await?;
				anyhow::ensure!(status == captaind::Status::Claimed, "receipt disappeared for {}", receipt.vtxo_id);
				store_claim(cfg, db, &claim.ok_or_else(|| anyhow::anyhow!("receipt omitted VTXO"))?).await?;
			}
		}
	}
	if let Some(id) = missing.first() {
		return Err(InvariantViolation(format!("captaind lacks {} local settlement(s), including {id}; restore with settlement_replay_ids before starting workers", missing.len())).into());
	}
	Ok(())
}

/// One pass. Infrastructure errors (DB, bitcoind) abort the tick and are
/// retried next tick; problems with a single coin quarantine that coin only.
async fn tick(
	cfg: &Config, sweep_spks: &[ScriptBuf], db: &mut tokio_postgres::Client, chain: &chain::Chain,
	journal: &mut journal::Journal, captaind: &mut captaind::Captaind, stats: &mut TickStats,
) -> anyhow::Result<()> {
	chain.check_wallet().await?;
	stats.tip = Some(chain.tip().await?);

	// Durable signed retries do not depend on captaind being available.
	reconcile_local(db, journal).await?;
	settle_inflight(cfg, db, chain, journal, stats).await?;
	reconcile_claims(cfg, db, journal, captaind).await?;
	reconcile_local(db, journal).await?;

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
	// Page past waiting coins as well as fee-filtered ones. Keep each query
	// bounded without allowing its oldest waiting rows to starve later coins.
	let mut cursor = (0, String::new());
	while claims_left > 0 {
		let page = captaind.page(false, p.grace_blocks, min_amount, &cursor, (p.max_batch as u32 * 20).min(256)).await?;
		let Some(last) = page.last() else { break; };
		cursor = (last.expiry, last.vtxo_id.clone());
		let ids = page.iter().map(|c| c.vtxo_id.clone()).collect::<Vec<_>>();
		let excluded = db::excluded(db, &ids).await?;
		for c in page {
			if claims_left <= 0 { break }
			stats.candidates += 1;
			if excluded.contains(&c.vtxo_id) || journal.contains(&c.vtxo_id) { continue }
			let reason = match process_coin(cfg, sweep_spks, db, chain, captaind, fee_rate, &c).await? {
				Outcome::Quarantine(reason) => reason,
				Outcome::Claimed => { stats.claims += 1; claims_left -= 1; info!(vtxo = %c.vtxo_id, "claimed"); continue },
				Outcome::Lost => { info!(vtxo = %c.vtxo_id, "user redeemed first; skipped"); continue },
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
	}

	pay_claimed(cfg, db, chain, journal, fee_rate, stats).await?;
	Ok(())
}

async fn process_coin(
	cfg: &Config, sweep_spks: &[ScriptBuf], db: &mut tokio_postgres::Client, chain: &chain::Chain,
	captaind: &mut captaind::Captaind, fee_rate: f64, c: &captaind::Candidate,
) -> anyhow::Result<Outcome> {
	let p = &cfg.policy;

	// Amount, key and anchor come from the stored VTXO; the DB is trusted.
	let vtxo: Vtxo = match Vtxo::deserialize(&c.vtxo) {
		Ok(v) => v,
		Err(e) => return Ok(Outcome::Quarantine(format!("undecodable vtxo: {e}"))),
	};
	let anchor = vtxo.chain_anchor();
	let amount = vtxo.amount().to_sat();
	// Leave unaffordable coins unclaimed so their owner can still refresh them.
	if !checks::affordable(amount, checks::fee_share_bound(fee_rate), p.max_fee_pct_per_payout) {
		return Ok(Outcome::Wait("fee share above max_fee_pct_per_payout"));
	}
	if chain.is_unspent(anchor).await? { return Ok(Outcome::Wait("anchor not swept yet")) }
	let (anchor_tx, _) = chain.tx(anchor.txid).await?;
	if let Err(e) = vtxo.validate_unsigned(&anchor_tx) {
		return Ok(Outcome::Quarantine(format!("invalid exit path: {e}")));
	}
	// Only this coin's output at each level belongs to its exit path. A
	// swept sibling, even in the same transaction, cannot settle this coin.
	let path = std::iter::once(anchor).chain(vtxo.transactions()
		.map(|t| OutPoint::new(t.tx.compute_txid(), t.output_idx as u32))).collect::<Vec<_>>();
	let mut swept = false;
	let hints = captaind.spenders(&path).await?;
	for (outpoint, spender) in path.into_iter().zip(hints) {
		if outpoint != anchor && chain.is_unspent(outpoint).await? { break; }
		let Some(spender) = spender else { continue; };
		let Ok(spender_txid) = Txid::from_str(&spender) else {
			return Ok(Outcome::Quarantine(format!("unparseable spender txid {spender:?}")));
		};
		let (spend_tx, confs) = match chain.tx(spender_txid).await {
			Ok(x) => x,
			Err(e) => {
				warn!(vtxo = %c.vtxo_id, %spender, "spender tx unavailable: {e:#}");
				continue;
			},
		};
		if checks::is_sweep_of(&spend_tx, outpoint, sweep_spks) {
			if confs < p.sweep_min_confs { return Ok(Outcome::Wait("sweep not deep enough")) }
			swept = true;
			break;
		}
	}
	if !swept { return Ok(Outcome::Wait("no confirmed sweep on this coin's exit path")) }

	match captaind.claim(&c.vtxo_id, p.grace_blocks).await? {
		(captaind::Status::Claimed, Some(claim)) => {
			store_claim(cfg, db, &claim).await?;
			Ok(Outcome::Claimed)
		},
		(captaind::Status::Busy, _) => Ok(Outcome::Wait("captaind operation holds the coin")),
		(captaind::Status::Ineligible, _) => Ok(Outcome::Lost),
		_ => anyhow::bail!("captaind claim omitted the VTXO"),
	}
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
			if let Err(e) = receipt::write(&cfg.journal_path.with_extension("receipts"), &built.tx, &expected, built.fee_sat) {
				warn!(%txid, "fee receipt unavailable; payout remains recoverable: {e:#}");
			}
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
	cfg: &Config, db: &mut tokio_postgres::Client, chain: &chain::Chain, journal: &mut journal::Journal,
	stats: &mut TickStats,
) -> anyhow::Result<()> {
	for (txid, raw) in journal.pending_transactions()? {
		if let Err(e) = ensure_receipt(cfg, db, chain, &txid).await {
			warn!(%txid, "fee receipt unavailable; retrying payment independently: {e:#}");
		}
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

/// Receipts are auxiliary accounting, never a prerequisite for recovering funds.
async fn ensure_receipt(
	cfg: &Config, db: &tokio_postgres::Client, chain: &chain::Chain, txid: &str,
) -> anyhow::Result<()> {
	let directory = cfg.journal_path.with_extension("receipts");
	if directory.join(format!("{txid}.json")).try_exists()? {
		// A prior rename may have succeeded before its directory sync failed.
		std::fs::File::open(&directory)?.sync_all()?;
		return Ok(());
	}
	let raw = db::raw_tx(db, txid).await?.ok_or_else(|| anyhow::anyhow!("receipt raw transaction unavailable: {txid}"))?;
	let tx = bitcoin::consensus::deserialize(&raw)?;
	let expected = db::receipt_amounts(db, txid).await?.into_iter().map(|(address, amount)| {
		Ok((Address::from_str(&address)?.require_network(cfg.network)?.script_pubkey(), amount))
	}).collect::<anyhow::Result<Vec<_>>>()?;
	receipt::write(&directory, &tx, &expected, chain.transaction_fee(&tx).await?)
}
