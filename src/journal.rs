//! Payout intents that survive a Postgres restore. Each new record contains
//! the entire batch: `<comma-separated coin ids> <txid> <raw tx hex>\n`.
//! A partial final record is ignored and replaced before the next append.

use std::collections::{HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub struct Journal {
	path: PathBuf,
	paid: HashMap<String, String>,
	raw: HashMap<String, String>,
	complete_len: u64,
	confirmed: HashSet<String>,
}

impl Journal {
	pub fn open(path: &Path) -> anyhow::Result<Journal> {
		let (mut paid, mut raw) = (HashMap::new(), HashMap::new());
		let mut complete_len = 0;
		if path.exists() {
			let mut reader = BufReader::new(File::open(path)?);
			let mut line = String::new();
			loop {
				line.clear();
				if reader.read_line(&mut line)? == 0 || !line.ends_with('\n') { break; }
				complete_len += line.len() as u64;
				let mut fields = line.split_whitespace();
				if let (Some(ids), Some(txid)) = (fields.next(), fields.next()) {
					// Also reads the original one-coin-per-line journal format.
					for id in ids.split(',') { paid.insert(id.to_owned(), txid.to_owned()); }
					if let Some(hex) = fields.next() { raw.insert(txid.to_owned(), hex.to_owned()); }
				}
			}
		}
		Ok(Journal { path: path.to_owned(), paid, raw, complete_len, confirmed: HashSet::new() })
	}

	pub fn ids(&self) -> Vec<String> { self.paid.keys().cloned().collect() }
	pub fn contains(&self, id: &str) -> bool { self.paid.contains_key(id) }
	pub fn txid(&self, id: &str) -> Option<&str> { self.paid.get(id).map(String::as_str) }
	pub fn has_transaction(&self, txid: &str) -> bool { self.raw.contains_key(txid) }

	pub fn raw_tx(&self, id: &str) -> Option<Vec<u8>> {
		bitcoin::hex::FromHex::from_hex(self.raw.get(self.paid.get(id)?)?).ok()
	}

	/// Legacy records may lack bytes. The database can fill them in, but
	/// recovery must stop if neither durable copy has the signed payment.
	pub fn check_transactions(&self) -> anyhow::Result<()> {
		for (id, txid) in &self.paid {
			anyhow::ensure!(self.raw.contains_key(txid),
				"journaled coin {id}, payout {txid}: raw transaction missing; recover the original transaction from the payout database or journal backup");
		}
		Ok(())
	}

	pub fn mark_confirmed(&mut self, txid: &str) { self.confirmed.insert(txid.to_owned()); }

	/// A restored row may need settlement even if another row was confirmed.
	pub fn mark_pending(&mut self, txid: &str) { self.confirmed.remove(txid); }

	/// The journal itself is the retry queue, even if Postgres lost the rows.
	pub fn pending_transactions(&self) -> anyhow::Result<Vec<(String, Vec<u8>)>> {
		self.raw.iter().filter(|(txid, _)| !self.confirmed.contains(*txid))
			.map(|(txid, hex)| Ok((txid.clone(), bitcoin::hex::FromHex::from_hex(hex)?))).collect()
	}

	/// Persist every batch member together before exposing its transaction.
	pub fn record(&mut self, ids: &[String], txid: &str, raw: &[u8]) -> anyhow::Result<()> {
		let hex = bitcoin::hex::DisplayHex::to_lower_hex_string(raw);
		let record = format!("{} {txid} {hex}\n", ids.join(","));
		let mut file = OpenOptions::new().create(true).append(true).open(&self.path)?;
		// Also removes an incomplete append from an earlier failed tick.
		file.set_len(self.complete_len)?;
		file.write_all(record.as_bytes())?;
		file.sync_all()?;
		// Repeat this after a failed append too: an existing file may have
		// been created by an earlier write whose directory sync failed.
		let parent = self.path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
		File::open(parent)?.sync_all()?;
		self.complete_len += record.len() as u64;
		for id in ids { self.paid.insert(id.clone(), txid.to_owned()); }
		self.raw.insert(txid.to_owned(), hex);
		Ok(())
	}
}

#[cfg(test)]
mod tests {
	use super::*;

	#[test]
	fn confirmed_batch_waits_for_every_restored_row() {
		let p = std::env::temp_dir().join(format!("journal-confirmed-{}", std::process::id()));
		let _ = std::fs::remove_file(&p);
		let mut j = Journal::open(&p).unwrap();
		j.record(&["a:0".into(), "b:1".into()], "tx1", &[1, 2]).unwrap();
		j.mark_confirmed("tx1");
		j.mark_pending("tx1");
		assert_eq!(j.pending_transactions().unwrap(), vec![("tx1".into(), vec![1, 2])]);
		j.mark_confirmed("tx1");
		assert!(j.pending_transactions().unwrap().is_empty());
		std::fs::remove_file(p).unwrap();
	}

	#[test]
	fn interrupted_append_never_exposes_part_of_a_batch() {
		let p = std::env::temp_dir().join(format!("journal-prefix-{}", std::process::id()));
		let _ = std::fs::remove_file(&p);
		Journal::open(&p).unwrap().record(&["a:0".into(), "b:1".into()], "tx1", &[1, 2]).unwrap();
		let complete = std::fs::read(&p).unwrap();
		let committed = b"z:0 tx0 ff\n";
		for length in 0..complete.len() {
			std::fs::write(&p, [committed.as_slice(), &complete[..length]].concat()).unwrap();
			let mut j = Journal::open(&p).unwrap();
			assert!(!j.contains("a:0") && !j.contains("b:1"), "partial batch exposed at byte {length}");
			j.record(&["c:0".into()], "tx2", &[3, 4]).unwrap();
			let recovered = Journal::open(&p).unwrap();
			let mut ids = recovered.ids();
			ids.sort();
			assert_eq!(ids, vec!["c:0".to_owned(), "z:0".to_owned()]);
			assert_eq!(recovered.raw_tx("z:0"), Some(vec![255]));
			assert_eq!(recovered.raw_tx("c:0"), Some(vec![3, 4]));
		}
		std::fs::remove_file(&p).unwrap();
	}

	#[test]
	fn missing_legacy_transaction_names_the_entitlement_and_payment() {
		let p = std::env::temp_dir().join(format!("journal-missing-{}", std::process::id()));
		std::fs::write(&p, "coin:0 payout-txid\n").unwrap();
		let mut j = Journal::open(&p).unwrap();
		let error = j.check_transactions().unwrap_err().to_string();
		assert!(error.contains("coin:0") && error.contains("payout-txid"));
		j.record(&["coin:0".into()], "payout-txid", &[1, 2]).unwrap();
		j.check_transactions().unwrap();
		std::fs::remove_file(&p).unwrap();
	}

	#[test]
	fn reads_legacy_complete_records() {
		let p = std::env::temp_dir().join(format!("journal-legacy-{}", std::process::id()));
		std::fs::write(&p, "a:0 tx1 0102\nb:1 tx1\n").unwrap();
		let j = Journal::open(&p).unwrap();
		assert!(j.contains("a:0") && j.contains("b:1"));
		assert_eq!(j.raw_tx("b:1"), Some(vec![1, 2]));
		std::fs::remove_file(&p).unwrap();
	}

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
		assert_eq!(std::fs::read_to_string(&p).unwrap(), "a:0,b:1 tx1 0102\n");
		std::fs::remove_file(&p).unwrap();
	}
}
