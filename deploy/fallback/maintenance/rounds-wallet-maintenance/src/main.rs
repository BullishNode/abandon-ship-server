//! Owned signet maintenance only. Never starts Server or handles client coins.
//! The host controller must stop all wallet writers before --commit.
use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, ensure};
use bitcoin::{Amount, FeeRate, Network, OutPoint, bip32, secp256k1::Secp256k1};
use bitcoin_ext::bdk::KEYCHAIN;
use serde::Deserialize;
use serde_json::{Value, json};
use server::bitcoind as bcd;
use server::nursery::NurseryTxKind;
use server::wallet::{BdkWalletExt, PersistedWallet, WalletKind};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Declaration {
	observed_unix: u64,
	simulator_dispatch_paused: bool,
	inflight_actions: u64,
	unknown_actions: u64,
	server_stopped: bool,
	rounds_xpub: String,
	inputs: Vec<OutPoint>,
	expected_input_sat: u64,
	output_count: usize,
	chunk_sat: u64,
	fee_sat_per_vb: u64,
	max_fee_sat: u64,
	max_derivation_index: u32,
}

fn append(file: &mut File, value: Value) -> anyhow::Result<()> {
	serde_json::to_writer(&mut *file, &value)?;
	file.write_all(b"\n")?;
	file.sync_all()?;
	Ok(())
}

async fn run() -> anyhow::Result<()> {
	let args: Vec<_> = std::env::args().collect();
	ensure!(args.len() == 5 && matches!(args[1].as_str(), "--plan" | "--commit"),
		"usage: rounds-wallet-maintenance --plan|--commit config.toml declaration.json private-intent.jsonl");
	let commit = args[1] == "--commit";
	let declared: Declaration = serde_json::from_slice(&fs::read(&args[3])?)?;
	let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
	ensure!(now >= declared.observed_unix && now - declared.observed_unix < 600, "stale declaration");
	ensure!(declared.simulator_dispatch_paused && declared.inflight_actions == 0 && declared.unknown_actions == 0,
		"simulator dispatch and unknown outcomes must be fenced");
	ensure!(declared.server_stopped, "host controller must verify every wallet writer is stopped");
	ensure!((1..=32).contains(&declared.output_count) && declared.chunk_sat >= 1000, "output bounds");
	ensure!(declared.fee_sat_per_vb > 0 && declared.max_fee_sat > 0, "fee bounds");
	let expected: BTreeSet<_> = declared.inputs.iter().copied().collect();
	ensure!(!expected.is_empty() && expected.len() == declared.inputs.len() && expected.len() <= 200, "input bounds");
	let parent = Path::new(&args[4]).parent().context("private intent directory required")?;
	ensure!(parent.metadata()?.permissions().mode() & 0o077 == 0, "intent directory must be private");
	let mut intent = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&args[4])?;
	File::open(parent)?.sync_all()?;
	append(&mut intent, json!({"state":"declared","observed_unix":now,"commit":commit,
		"expected_inputs":expected,"expected_input_sat":declared.expected_input_sat,"max_fee_sat":declared.max_fee_sat}))?;

	let cfg = server::config::Config::load(Path::new(&args[2]))?;
	ensure!(cfg.network == Network::Signet, "maintenance is signet only");
	let db = server::database::Db::connect(&cfg.postgres).await?;
	let pending: i64 = db.read(async |t| {
		Ok(t.query_one("SELECT count(*) FROM nursery_tx WHERE confirmed_at_height IS NULL AND abandoned_at IS NULL", &[]).await?.get(0))
	}).await?;
	ensure!(pending == 0, "fragmentation waits for existing nursery transactions; upgrade uses a different controller");
	let existing: i64 = db.read(async |t| {
		Ok(t.query_one("SELECT count(*) FROM wallet_changeset WHERE kind::text='rounds'", &[]).await?.get(0))
	}).await?;
	ensure!(existing > 0, "never create a missing server wallet");
	let bitcoind = bcd::build_client(&cfg.bitcoind.url, cfg.bitcoind.auth())?;
	bcd::require_network(&bitcoind, Network::Signet).await?;
	let genesis: String = bitcoind.call_raw("getblockhash", &[0.into()]).await?;
	ensure!(genesis == "00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6", "unexpected signet chain");
	let tip = bcd::tip(&bitcoind).await?;
	let deep_tip = bcd::deep_tip(&bitcoind).await?;
	let seed = server::wallet::read_mnemonic_from_datadir(&cfg.data_dir)?.to_seed("");
	let master = bip32::Xpriv::new_master(Network::Signet, &seed)?;
	let secp = Secp256k1::new();
	let rounds = master.derive_priv(&secp, &[WalletKind::Rounds.child_number()])?;
	ensure!(bip32::Xpub::from_priv(&secp, &rounds).to_string() == declared.rounds_xpub, "rounds identity mismatch");
	let mut wallet = PersistedWallet::load_from_xpriv(db.clone(), bitcoind.clone(), Network::Signet,
		&rounds, WalletKind::Rounds, deep_tip, cfg.min_trusted_confs).await?;
	let unspent: Vec<_> = wallet.list_unspent().collect();
	ensure!(unspent.iter().all(|u| u.chain_position.is_confirmed()), "unconfirmed wallet outputs present");
	ensure!(unspent.iter().map(|u| u.outpoint).collect::<BTreeSet<_>>() == expected, "wallet input set changed");
	let total = unspent.iter().try_fold(0u64, |sum, u| sum.checked_add(u.txout.value.to_sat()).context("input overflow"))?;
	ensure!(total == declared.expected_input_sat, "input amount changed");
	for utxo in &unspent {
		let live: Option<Value> = bitcoind.call_raw("gettxout", &[utxo.outpoint.txid.to_string().into(),
			utxo.outpoint.vout.into(), true.into()]).await?;
		let live = live.context("input was spent or is missing in Core")?;
		ensure!(live["confirmations"].as_u64().unwrap_or(0) >= u64::from(cfg.min_trusted_confs), "input confirmation depth");
		ensure!(live["scriptPubKey"]["hex"].as_str() == Some(utxo.txout.script_pubkey.to_hex_string().as_str()), "Core input script differs");
		let core_value = Amount::from_btc(live["value"].as_f64().context("Core input value missing")?)?;
		ensure!(core_value == utxo.txout.value, "Core input value differs");
	}
	let mut scripts = Vec::new();
	for _ in 0..declared.output_count {
		let address = wallet.reveal_next_address(KEYCHAIN);
		ensure!(address.index <= declared.max_derivation_index, "extend public descriptor range before spending");
		scripts.push(address.address.script_pubkey());
	}
	let mut builder = wallet.build_tx();
	builder.add_utxos(&declared.inputs)?.manually_selected_only();
	builder.fee_rate(FeeRate::from_sat_per_vb(declared.fee_sat_per_vb).context("fee rate overflow")?);
	for script in &scripts[..scripts.len()-1] {
		builder.add_recipient(script.clone(), Amount::from_sat(declared.chunk_sat));
	}
	builder.drain_to(scripts.last().unwrap().clone());
	let psbt = builder.finish()?;
	let fee = psbt.fee()?.to_sat();
	ensure!(fee <= declared.max_fee_sat, "fee exceeds declaration");
	ensure!(psbt.unsigned_tx.input.iter().map(|i| i.previous_output).collect::<BTreeSet<_>>() == expected, "builder changed inputs");
	ensure!(psbt.unsigned_tx.output.len() == declared.output_count, "unexpected output count");
	for output in &psbt.unsigned_tx.output {
		ensure!(scripts.contains(&output.script_pubkey) && wallet.is_mine(output.script_pubkey.clone()), "non-rounds output");
		ensure!(output.value >= output.script_pubkey.minimal_non_dust() && output.value.to_sat() >= 1000, "small output");
	}
	let planned = json!({"state":"planned","tip":tip.height.to_u32(),"fee_sat":fee,
		"inputs":expected,"outputs":psbt.unsigned_tx.output.iter().map(|o| json!({"spk":o.script_pubkey.to_hex_string(),"sat":o.value.to_sat()})).collect::<Vec<_>>()});
	append(&mut intent, planned)?;
	if !commit { println!("PLAN_ONLY: no database mutation or broadcast"); return Ok(()); }
	let tx = wallet.finish_tx(psbt)?;
	let txid = tx.compute_txid();
	append(&mut intent, json!({"state":"signed_not_committed","txid":txid,"raw_hex":bitcoin::consensus::encode::serialize_hex(&tx)}))?;
	// Persist the signed spend and derivations in the same transaction as its
	// nursery row. A crash must not leave the wallet forgetting spent inputs.
	wallet.commit_tx(&tx);
	let metadata = wallet.staged().cloned().context("signed spend and derivations must be staged")?;
	db.write(async |t| {
		t.store_changeset(WalletKind::Rounds, &metadata).await?;
		t.upsert_nursery_tx(&tx, NurseryTxKind::Internal,
			tip.height.checked_add(bitcoin_ext::BlockDelta::new(6)).context("target height overflow")?).await?;
		Ok(())
	}).await.context("commit may be unknown; inspect the immutable intent and database before any new signing")?;
	append(&mut intent, json!({"state":"committed_pending_broadcast","txid":txid}))?;
	wallet.take_staged();
	let stored = db.read(async |t| t.get_nursery_raw_tx(txid).await).await?.context("nursery transaction missing")?;
	ensure!(stored == tx, "nursery bytes differ");
	bcd::broadcast_tx(&bitcoind, &tx).await.context("broadcast unknown; retry identical stored transaction only")?;
	append(&mut intent, json!({"state":"broadcast_requested","txid":txid}))?;
	println!("{}", txid);
	Ok(())
}

#[tokio::main(worker_threads = 2)]
async fn main() {
	if let Err(error) = run().await {
		// Contexts contain only our checks/RPC outcomes; no config or keys are printed.
		eprintln!("maintenance refused or interrupted: {error:#}");
		std::process::exit(1);
	}
}
