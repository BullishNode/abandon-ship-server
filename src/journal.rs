//! Append-only payout journal on the sidecar's own disk.
//!
//! The ledger lives in captaind's Postgres, which others can write and which
//! can be restored from an older backup.
//! The journal is the payout record that survives both: a coin listed here is
//! never paid again, whatever the DB says. One line per paid coin:
//! `<vtxo_id> <txid> <raw tx hex>`, written and fsynced before the tx is
//! broadcast, so a restore can still broadcast a tx the DB no longer has.
//! Lines without the raw tx (older journals) are read too.

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub struct Journal {
	path: PathBuf,
	/// vtxo id -> txid
	paid: HashMap<String, String>,
	/// txid -> raw tx hex
	raw: HashMap<String, String>,
}

impl Journal {
	pub fn open(path: &Path) -> anyhow::Result<Journal> {
		let (mut paid, mut raw) = (HashMap::new(), HashMap::new());
		if path.exists() {
			for line in BufReader::new(File::open(path)?).lines() {
				let line = line?;
				let mut f = line.split_whitespace();
				if let (Some(id), Some(txid)) = (f.next(), f.next()) {
					paid.insert(id.to_string(), txid.to_string());
					if let Some(hex) = f.next() {
						raw.insert(txid.to_string(), hex.to_string());
					}
				}
			}
		}
		Ok(Journal { path: path.to_path_buf(), paid, raw })
	}

	pub fn ids(&self) -> Vec<String> {
		self.paid.keys().cloned().collect()
	}

	pub fn contains(&self, vtxo_id: &str) -> bool {
		self.paid.contains_key(vtxo_id)
	}

	/// The journaled payout tx of a coin, if the journal has its raw tx.
	pub fn raw_tx(&self, vtxo_id: &str) -> Option<Vec<u8>> {
		let hex = self.raw.get(self.paid.get(vtxo_id)?)?;
		bitcoin::hex::FromHex::from_hex(hex).ok()
	}

	/// Append and fsync before broadcasting.
	pub fn record(&mut self, vtxo_ids: &[String], txid: &str, raw: &[u8]) -> anyhow::Result<()> {
		let hex = bitcoin::hex::DisplayHex::to_lower_hex_string(raw);
		let mut f = OpenOptions::new().create(true).append(true).open(&self.path)?;
		for id in vtxo_ids {
			writeln!(f, "{id} {txid} {hex}")?;
		}
		f.sync_all()?;
		for id in vtxo_ids {
			self.paid.insert(id.clone(), txid.to_string());
		}
		self.raw.insert(txid.to_string(), hex);
		Ok(())
	}
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn survives_reopen() {
		let p = std::env::temp_dir().join(format!("journal-test-{}", std::process::id()));
		let _ = std::fs::remove_file(&p);
		let mut j = Journal::open(&p).unwrap();
		assert!(!j.contains("a:0"));
		j.record(&["a:0".into(), "b:1".into()], "tx1", &[1, 2]).unwrap();
		let j2 = Journal::open(&p).unwrap();
		assert!(j2.contains("a:0") && j2.contains("b:1") && !j2.contains("c:0"));
		assert_eq!(j2.raw_tx("b:1"), Some(vec![1, 2]));
		std::fs::remove_file(&p).unwrap();
	}
}
