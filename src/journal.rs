//! Append-only payout journal on the sidecar's own disk.
//!
//! The ledger lives in captaind's Postgres, which others can write and which
//! can be restored from an older backup (#109, #110, variations C8/C10/C11).
//! The journal is the payout record that survives both: a coin listed here is
//! never paid again, whatever the DB says. One line per paid coin:
//! `<vtxo_id> <txid>`, written and fsynced before the tx is broadcast.

use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub struct Journal {
	path: PathBuf,
	paid: HashSet<String>,
}

impl Journal {
	pub fn open(path: &Path) -> anyhow::Result<Journal> {
		let mut paid = HashSet::new();
		if path.exists() {
			for line in BufReader::new(File::open(path)?).lines() {
				let line = line?;
				if let Some(id) = line.split_whitespace().next() {
					paid.insert(id.to_string());
				}
			}
		}
		Ok(Journal { path: path.to_path_buf(), paid })
	}

	pub fn ids(&self) -> Vec<String> {
		self.paid.iter().cloned().collect()
	}

	pub fn contains(&self, vtxo_id: &str) -> bool {
		self.paid.contains(vtxo_id)
	}

	/// Append and fsync before broadcasting.
	pub fn record(&mut self, vtxo_ids: &[String], txid: &str) -> anyhow::Result<()> {
		let mut f = OpenOptions::new().create(true).append(true).open(&self.path)?;
		for id in vtxo_ids {
			writeln!(f, "{id} {txid}")?;
		}
		f.sync_all()?;
		self.paid.extend(vtxo_ids.iter().cloned());
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
		j.record(&["a:0".into(), "b:1".into()], "tx1").unwrap();
		let j2 = Journal::open(&p).unwrap();
		assert!(j2.contains("a:0") && j2.contains("b:1") && !j2.contains("c:0"));
		std::fs::remove_file(&p).unwrap();
	}
}
