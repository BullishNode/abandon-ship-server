//! Public fee receipts. Serve only this directory, never the private journal.

use std::fs::{self, File};
use std::io::Write;
use std::path::Path;

use bitcoin::{ScriptBuf, Transaction};
use serde::Serialize;

#[derive(Serialize)]
struct Output {
	vout: u32,
	amount_sat: u64,
	fee_sat: u64,
}

#[derive(Serialize)]
struct Receipt {
	txid: bitcoin::Txid,
	outputs: Vec<Output>,
}

/// One entry per output, including the combined deduction when coins share a
/// key. The check also prevents incomplete restored ledger rows from producing
/// an incorrect fee receipt. No VTXO IDs or private wallet information is public.
pub fn write(
	directory: &Path, tx: &Transaction, expected: &[(ScriptBuf, u64)], fee_sat: u64,
) -> anyhow::Result<()> {
	anyhow::ensure!(!expected.is_empty(), "no receipt entitlements");
	let mut outputs = Vec::with_capacity(expected.len());
	for (script, gross) in expected {
		let mut matches = tx.output.iter().enumerate().filter(|(_, o)| &o.script_pubkey == script);
		let (vout, output) = matches.next().ok_or_else(|| anyhow::anyhow!("receipt output missing"))?;
		anyhow::ensure!(matches.next().is_none(), "duplicate receipt output");
		let amount_sat = output.value.to_sat();
		let fee_sat = gross.checked_sub(amount_sat).ok_or_else(|| anyhow::anyhow!("incomplete receipt entitlement"))?;
		outputs.push(Output { vout: vout as u32, amount_sat, fee_sat });
	}
	outputs.sort_by_key(|o| o.vout);
	anyhow::ensure!(outputs.windows(2).all(|w| w[0].vout != w[1].vout), "duplicate receipt script");
	anyhow::ensure!(outputs.iter().map(|o| o.fee_sat).sum::<u64>() == fee_sat, "receipt deductions do not equal mining fee");
	let receipt = Receipt { txid: tx.compute_txid(), outputs };
	fs::create_dir_all(directory)?;
	let parent = directory.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
	File::open(parent)?.sync_all()?;
	let temporary = directory.join(format!("{}.tmp", receipt.txid));
	let mut file = File::create(&temporary)?;
	serde_json::to_writer(&mut file, &receipt)?;
	file.write_all(b"\n")?;
	file.sync_all()?;
	fs::rename(temporary, directory.join(format!("{}.json", receipt.txid)))?;
	File::open(directory)?.sync_all()?;
	Ok(())
}

#[cfg(test)]
mod tests {
	use super::*;
	use bitcoin::{absolute, transaction, Amount, TxOut};

	#[test]
	fn receipt_uses_output_deductions_and_excludes_change() {
		let a = ScriptBuf::from_bytes(vec![1]);
		let b = ScriptBuf::from_bytes(vec![2]);
		let tx = Transaction {
			version: transaction::Version::TWO, lock_time: absolute::LockTime::ZERO, input: vec![],
			output: vec![
				TxOut { script_pubkey: b.clone(), value: Amount::from_sat(49_850) },
				TxOut { script_pubkey: ScriptBuf::new(), value: Amount::from_sat(1_000) },
				TxOut { script_pubkey: a.clone(), value: Amount::from_sat(69_849) },
			],
		};
		let dir = std::env::temp_dir().join(format!("payout-receipt-{}", std::process::id()));
		let expected = [(a.clone(), 40_000 + 30_000), (b.clone(), 50_000)];
		write(&dir, &tx, &expected, 301).unwrap();
		let bytes = fs::read(dir.join(format!("{}.json", tx.compute_txid()))).unwrap();
		let receipt: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
		assert_eq!(receipt["outputs"], serde_json::json!([
			{"vout": 0, "amount_sat": 49_850, "fee_sat": 150},
			{"vout": 2, "amount_sat": 69_849, "fee_sat": 151},
		]));
		// Lost records cannot reduce the fee in an existing receipt.
		assert!(write(&dir, &tx, &[(a, 69_999), (b, 50_000)], 301).is_err());
		assert_eq!(fs::read(dir.join(format!("{}.json", tx.compute_txid()))).unwrap(), bytes);
		// A failed replacement leaves the complete receipt intact; retry works.
		let temporary = dir.join(format!("{}.tmp", tx.compute_txid()));
		fs::create_dir(&temporary).unwrap();
		assert!(write(&dir, &tx, &expected, 301).is_err());
		assert_eq!(fs::read(dir.join(format!("{}.json", tx.compute_txid()))).unwrap(), bytes);
		fs::remove_dir(temporary).unwrap();
		write(&dir, &tx, &expected, 301).unwrap();
		assert_eq!(fs::read(dir.join(format!("{}.json", tx.compute_txid()))).unwrap(), bytes);
		fs::remove_dir_all(dir).unwrap();
	}
}
