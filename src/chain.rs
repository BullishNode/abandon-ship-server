//! bitcoind RPC (blocking client, called from spawn_blocking). The chain is
//! authority for sweep transactions; captaind records spender hints.

use std::str::FromStr;
use std::sync::Arc;

use bitcoin::{Address, Amount, OutPoint, Transaction, Txid};
use bitcoincore_rpc::{Auth, Client, RpcApi};

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
			// feerate is BTC/kvB; 1 BTC/kvB = 100_000 sat/vB. Go through integer
			// sat/kvB: `walletcreatefundedpsbt` rejects a fee_rate with more than
			// 3 decimals ("Invalid amount"), e.g. 3.003e-5 * 1e5 = 3.0029999999999997.
			Ok(v.get("feerate").and_then(|f| f.as_f64()).map(|btc_kvb| (btc_kvb * 1e8).round() / 1000.0))
		}).await
	}

	pub async fn tip(&self) -> anyhow::Result<u32> {
		self.run(|c| { let h: u64 = c.call("getblockcount", &[])?; Ok(h as u32) }).await
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

	/// True if bitcoind currently reports the outpoint as unspent.
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
			// Untyped calls only: typed decoding broke on Core 31.
			let n = outputs.len();
			let outs: serde_json::Map<String, serde_json::Value> = outputs.into_iter()
				.map(|(a, s)| (a, serde_json::Value::from(Amount::from_sat(s).to_btc()))).collect();
			let opts = serde_json::json!({
				"subtractFeeFromOutputs": (0..n).collect::<Vec<_>>(),
				"replaceable": true,
				// The rate already checked by the fee gate; never let the
				// wallet pick its own (or fall back) behind our back.
				"fee_rate": fee_rate_sat_vb,
			});
			let funded: serde_json::Value = c.call("walletcreatefundedpsbt",
				&[serde_json::json!([]), serde_json::Value::Object(outs), 0.into(), opts])?;
			let psbt = funded["psbt"].as_str().ok_or_else(|| anyhow::anyhow!("no psbt"))?;
			let fee_btc = funded["fee"].as_f64().ok_or_else(|| anyhow::anyhow!("no fee"))?;
			let signed: serde_json::Value = c.call("walletprocesspsbt", &[psbt.into(), true.into()])?;
			let spsbt = signed["psbt"].as_str().ok_or_else(|| anyhow::anyhow!("no signed psbt"))?;
			let fin: serde_json::Value = c.call("finalizepsbt", &[spsbt.into(), true.into()])?;
			anyhow::ensure!(fin["complete"].as_bool() == Some(true), "payout psbt not complete");
			let hex = fin["hex"].as_str().ok_or_else(|| anyhow::anyhow!("no tx hex"))?;
			let tx: Transaction = bitcoin::consensus::encode::deserialize_hex(hex)?;
			let raw = bitcoin::consensus::serialize(&tx);
			Ok(BuiltPayout { tx, raw, fee_sat: Amount::from_btc(fee_btc)?.to_sat() })
		}).await
	}

	/// Reserve any still-unspent inputs of a stored payout. Wallet locks are
	/// rebuilt from the durable transaction after a node or sidecar restart.
	pub async fn reserve_inputs(&self, raw: &[u8]) -> anyhow::Result<()> {
		let tx: Transaction = bitcoin::consensus::deserialize(raw)?;
		self.run(move |c| {
			let locked: Vec<serde_json::Value> = c.call("listlockunspent", &[])?;
			let mut reserve = Vec::new();
			for input in tx.input {
				let op = input.previous_output;
				let entry = serde_json::json!({"txid": op.txid.to_string(), "vout": op.vout});
				if locked.contains(&entry) { continue; }
				let unspent: serde_json::Value = c.call("gettxout", &[op.txid.to_string().into(), op.vout.into(), true.into()])?;
				if !unspent.is_null() { reserve.push(entry); }
			}
			if !reserve.is_empty() {
				let ok: bool = c.call("lockunspent", &[false.into(), reserve.into()])?;
				anyhow::ensure!(ok, "could not reserve stored payout inputs");
			}
			Ok(())
		}).await
	}

	/// Broadcast; "already in mempool / known" counts as success.
	pub async fn broadcast(&self, raw: Vec<u8>) -> anyhow::Result<()> {
		let hex = bitcoin::hex::DisplayHex::to_lower_hex_string(&raw[..]);
		self.run(move |c| match c.call::<serde_json::Value>("sendrawtransaction", &[hex.into()]) {
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
