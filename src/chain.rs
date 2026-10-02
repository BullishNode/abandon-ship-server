//! bitcoind RPC (blocking client, called from spawn_blocking). The chain is
//! the source of truth; captaind's DB only supplies hints.

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Arc;

use bitcoin::{Address, Amount, OutPoint, Transaction, Txid};
use bitcoincore_rpc::json::WalletCreateFundedPsbtOptions;
use bitcoincore_rpc::{Auth, Client, RpcApi};

#[derive(Clone)]
pub struct Chain {
	rpc: Arc<Client>,
}

pub struct BuiltPayout {
	pub tx: Transaction,
	pub raw: Vec<u8>,
	pub fee_sat: u64,
}

impl Chain {
	pub fn new(url: &str, user: &str, pass: &str) -> anyhow::Result<Chain> {
		let rpc = Client::new(url, Auth::UserPass(user.into(), pass.into()))?;
		Ok(Chain { rpc: Arc::new(rpc) })
	}

	async fn run<T: Send + 'static>(
		&self, f: impl FnOnce(&Client) -> anyhow::Result<T> + Send + 'static,
	) -> anyhow::Result<T> {
		let rpc = self.rpc.clone();
		tokio::task::spawn_blocking(move || f(&rpc)).await?
	}

	/// Fails loudly if the payout wallet is not loaded (bitcoind does not
	/// reload wallets after a restart unless `load_on_startup` was set).
	pub async fn check_wallet(&self) -> anyhow::Result<()> {
		self.run(|c| { let _: serde_json::Value = c.call("getwalletinfo", &[])?; Ok(()) }).await
	}

	/// bitcoind's fee estimate in sat/vB for `conf_target`, or None if the
	/// estimator has no answer (fresh node, broken estimator, offline).
	pub async fn estimate_fee_rate(&self, conf_target: u16) -> anyhow::Result<Option<f64>> {
		self.run(move |c| {
			let v: serde_json::Value = c.call("estimatesmartfee", &[conf_target.into()])?;
			// feerate is BTC/kvB; 1 BTC/kvB = 100_000 sat/vB
			Ok(v.get("feerate").and_then(|f| f.as_f64()).map(|btc_kvb| btc_kvb * 100_000.0))
		}).await
	}

	pub async fn tip(&self) -> anyhow::Result<u32> {
		self.run(|c| Ok(c.get_block_count()? as u32)).await
	}

	/// A transaction and its confirmations (0 = mempool). Requires txindex.
	///
	/// Uses an untyped call and decodes the hex itself: bitcoincore-rpc 0.19's
	/// typed result cannot parse Core 31's `anchor` (P2A) script type, which
	/// every Ark tree tx carries.
	pub async fn tx(&self, txid: Txid) -> anyhow::Result<(Transaction, u32)> {
		self.run(move |c| {
			let v: serde_json::Value = c.call("getrawtransaction", &[txid.to_string().into(), 1.into()])?;
			let hex = v.get("hex").and_then(|h| h.as_str())
				.ok_or_else(|| anyhow::anyhow!("getrawtransaction: no hex"))?;
			let tx: Transaction = bitcoin::consensus::encode::deserialize_hex(hex)?;
			let confs = v.get("confirmations").and_then(|c| c.as_u64()).unwrap_or(0) as u32;
			Ok((tx, confs))
		}).await
	}

	/// true if the outpoint is currently unspent (or unknown) per bitcoind.
	pub async fn is_unspent(&self, op: OutPoint) -> anyhow::Result<bool> {
		self.run(move |c| {
			let v: serde_json::Value = c.call("gettxout", &[op.txid.to_string().into(), op.vout.into(), true.into()])?;
			Ok(!v.is_null())
		}).await
	}

	pub async fn is_mine(&self, spk: bitcoin::ScriptBuf, network: bitcoin::Network) -> anyhow::Result<bool> {
		self.run(move |c| {
			let addr = Address::from_script(&spk, network)?;
			let v: serde_json::Value = c.call("getaddressinfo", &[addr.to_string().into()])?;
			Ok(v.get("ismine").and_then(|m| m.as_bool()).unwrap_or(false))
		}).await
	}

	/// Fund, sign and finalize one tx paying `outputs`, with the fee taken
	/// equally from all outputs. Does not broadcast.
	pub async fn build_payout(&self, outputs: Vec<(String, u64)>, fee_rate_sat_vb: f64) -> anyhow::Result<BuiltPayout> {
		self.run(move |c| {
			let n = outputs.len() as u16;
			let outs: HashMap<String, Amount> = outputs.into_iter()
				.map(|(a, s)| (a, Amount::from_sat(s))).collect();
			let opts = WalletCreateFundedPsbtOptions {
				subtract_fee_from_outputs: (0..n).collect(),
				replaceable: Some(true),
				// The rate we already checked against the caps; never let the
				// wallet pick its own (or fall back) behind our back.
				fee_rate: Some(Amount::from_sat((fee_rate_sat_vb * 1000.0).ceil() as u64)),
				..Default::default()
			};
			let funded = c.wallet_create_funded_psbt(&[], &outs, None, Some(opts), None)?;
			let signed = c.wallet_process_psbt(&funded.psbt, Some(true), None, None)?;
			let fin = c.finalize_psbt(&signed.psbt, Some(true))?;
			anyhow::ensure!(fin.complete, "payout psbt not complete");
			let raw = fin.hex.ok_or_else(|| anyhow::anyhow!("no tx hex"))?;
			let tx: Transaction = bitcoin::consensus::deserialize(&raw)?;
			Ok(BuiltPayout { tx, raw, fee_sat: funded.fee.to_sat() })
		}).await
	}

	/// Broadcast; "already in mempool / known" counts as success.
	pub async fn broadcast(&self, raw: Vec<u8>) -> anyhow::Result<()> {
		self.run(move |c| match c.send_raw_transaction(raw.as_slice()) {
			Ok(_) => Ok(()),
			Err(e) if e.to_string().contains("already") => Ok(()),
			Err(e) => Err(e.into()),
		}).await
	}

	pub async fn confirmations(&self, txid: &str) -> anyhow::Result<u32> {
		let txid = Txid::from_str(txid)?;
		Ok(self.tx(txid).await?.1)
	}
}
