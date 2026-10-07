//! Pure checks, kept free of I/O so they can be unit-tested.

use std::collections::HashMap;

use bitcoin::{OutPoint, ScriptBuf, Transaction};

/// P2TR dust limit.
pub const P2TR_DUST_SAT: u64 = 330;
/// Worst-case vbytes one output carries when it is alone in a batch
/// (output + two inputs + change + tx overhead).
pub const LONE_OUTPUT_VB: f64 = 230.0;

/// Upper bound of one output's fee share at `rate` sat/vB.
pub fn fee_share_bound(rate_sat_vb: f64) -> u64 {
	(rate_sat_vb * LONE_OUTPUT_VB).ceil() as u64
}

/// A payout of `value` is worth making if its fee share is at most `pct`% of
/// it and at least dust remains. Otherwise the coin is left alone (it stays
/// refreshable by its owner) until fees fall.
pub fn affordable(value: u64, share: u64, pct: u64) -> bool {
	100 * share <= pct * value && value >= share + P2TR_DUST_SAT
}

/// The smallest value `affordable` accepts.
pub fn min_affordable(share: u64, pct: u64) -> u64 {
	(100 * share).div_ceil(pct).max(share + P2TR_DUST_SAT)
}

/// P2A fee-anchor script (OP_1 <0x4e73>), as used by captaind's claim txs.
fn is_p2a(spk: &ScriptBuf) -> bool {
	spk.as_bytes() == [0x51, 0x02, 0x4e, 0x73]
}

/// A funding output counts as swept only if its spender pays every
/// non-OP_RETURN, non-P2A output to a configured sweep script. This mirrors
/// captaind's own "Claim" classification (`server/src/watchman/mod.rs`).
pub fn is_sweep(spender: &Transaction, sweep_spks: &[ScriptBuf]) -> bool {
	let mut paid_any = false;
	for o in &spender.output {
		if o.script_pubkey.is_op_return() || is_p2a(&o.script_pubkey) {
			continue;
		}
		if !sweep_spks.contains(&o.script_pubkey) {
			return false;
		}
		paid_any = true;
	}
	paid_any
}

/// The chain transaction must spend the precise output on the coin's path.
pub fn is_sweep_of(spender: &Transaction, outpoint: OutPoint, sweep_spks: &[ScriptBuf]) -> bool {
	spender.input.iter().any(|input| input.previous_output == outpoint)
		&& is_sweep(spender, sweep_spks)
}

/// Verify a built payout before it is stored:
/// - every expected script gets exactly one output, worth at most the expected
///   amount and at least expected minus the whole fee;
/// - every output's fee share is within `max_fee_pct` and leaves dust;
/// - recipient deductions equal the entire mining fee;
/// - at most one other output (change), which the caller checks is ours.
///
/// Returns the change output's script, if any.
pub fn verify_payout(
	tx: &Transaction,
	expected: &[(ScriptBuf, u64)],
	fee_sat: u64,
	max_fee_pct: u64,
) -> anyhow::Result<Option<ScriptBuf>> {
	let total: u64 = expected.iter().map(|(_, a)| a).sum();
	anyhow::ensure!(total > 0, "empty payout");

	let mut want: HashMap<&ScriptBuf, u64> = HashMap::new();
	for (spk, amt) in expected {
		anyhow::ensure!(want.insert(spk, *amt).is_none(), "duplicate payout script");
	}
	let mut change = None;
	let mut deducted = 0;
	for o in &tx.output {
		match want.remove(&o.script_pubkey) {
			Some(amt) => {
				let v = o.value.to_sat();
				anyhow::ensure!(v <= amt && v + fee_sat >= amt,
					"output {v} for expected {amt} outside fee bound");
				deducted += amt - v;
				anyhow::ensure!(affordable(amt, amt - v, max_fee_pct),
					"output {v} for expected {amt}: fee share above {max_fee_pct}% or below dust");
			},
			None => {
				anyhow::ensure!(change.is_none(), "more than one unexpected output");
				change = Some(o.script_pubkey.clone());
			},
		}
	}
	anyhow::ensure!(want.is_empty(), "{} expected output(s) missing", want.len());
	anyhow::ensure!(deducted == fee_sat, "recipient deductions {deducted} do not equal mining fee {fee_sat}");
	Ok(change)
}

#[cfg(test)]
mod tests {
	use super::*;
	use bitcoin::{absolute, transaction, Amount, TxOut};

	fn spk(b: u8) -> ScriptBuf { ScriptBuf::from_bytes(vec![0x00, 0x14, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b, b]) }
	fn tx(outs: Vec<(ScriptBuf, u64)>) -> Transaction {
		Transaction {
			version: transaction::Version::TWO,
			lock_time: absolute::LockTime::ZERO,
			input: vec![],
			output: outs.into_iter().map(|(s, v)| TxOut { script_pubkey: s, value: Amount::from_sat(v) }).collect(),
		}
	}

	#[test]
	fn affordability() {
		// 20%: share 100 needs value >= 500 and >= 430 (dust): 500 ok, 499 not
		assert!(affordable(500, 100, 20));
		assert!(!affordable(499, 100, 20));
		// dust dominates at tiny shares: 340 with share 10 leaves 330
		assert!(affordable(340, 10, 20));
		assert!(!affordable(339, 10, 20));
		assert_eq!(fee_share_bound(1.0), 230);
		for (share, pct) in [(100, 20), (10, 20), (690, 20), (690, 1), (7, 99)] {
			let m = min_affordable(share, pct);
			assert!(affordable(m, share, pct) && !affordable(m - 1, share, pct));
		}
	}

	#[test]
	fn sweep_only_to_sweep_scripts() {
		let sweep = spk(1);
		let p2a = ScriptBuf::from_bytes(vec![0x51, 0x02, 0x4e, 0x73]);
		assert!(is_sweep(&tx(vec![(sweep.clone(), 1000), (p2a.clone(), 0)]), std::slice::from_ref(&sweep)));
		assert!(!is_sweep(&tx(vec![(sweep.clone(), 1000), (spk(2), 500)]), std::slice::from_ref(&sweep)));
		assert!(!is_sweep(&tx(vec![(p2a, 0)]), &[sweep]));
	}

	#[test]
	fn a_swept_sibling_is_not_this_coins_sweep() {
		use bitcoin::{hashes::Hash, Txid, TxIn};
		let txid = Txid::from_byte_array([7; 32]);
		let a = OutPoint::new(txid, 0);
		let b = OutPoint::new(txid, 1);
		let sweep = spk(1);
		let mut spending_b = tx(vec![(sweep.clone(), 1000)]);
		spending_b.input.push(TxIn { previous_output: b, ..Default::default() });
		assert!(is_sweep_of(&spending_b, b, std::slice::from_ref(&sweep)));
		assert!(!is_sweep_of(&spending_b, a, &[sweep]));
	}

	#[test]
	fn all_mining_fees_come_from_receivers() {
		let exp = vec![(spk(1), 10_000), (spk(2), 20_000)];
		// Each output separately satisfies the fee bound in all three cases.
		for (a, b) in [(9_950, 19_950), (9_800, 19_800)] {
			assert!(verify_payout(&tx(vec![(spk(1), a), (spk(2), b)]), &exp, 300, 20).is_err());
		}
		assert!(verify_payout(&tx(vec![(spk(1), 9_850), (spk(2), 19_850)]), &exp, 300, 20).is_ok());
	}

	#[test]
	fn payout_verification() {
		let exp = vec![(spk(1), 10_000), (spk(2), 20_000)];
		// fee 300 split 150/150, plus change
		let ok = tx(vec![(spk(1), 9_850), (spk(2), 19_850), (spk(9), 5_000)]);
		assert_eq!(verify_payout(&ok, &exp, 300, 20).unwrap(), Some(spk(9)));
		// overpay
		assert!(verify_payout(&tx(vec![(spk(1), 10_001), (spk(2), 19_850)]), &exp, 300, 20).is_err());
		// missing output
		assert!(verify_payout(&tx(vec![(spk(1), 9_850)]), &exp, 300, 20).is_err());
		// two unknown outputs
		assert!(verify_payout(&tx(vec![(spk(1), 9_850), (spk(2), 19_850), (spk(8), 1), (spk(9), 1)]), &exp, 300, 20).is_err());
		// per-output share cap: 150 of 10_000 is 1.5%, fine at 20%, not at 1%
		assert!(verify_payout(&ok, &exp, 300, 1).is_err());
	}
}
