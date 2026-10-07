//! Public coin facts for the comparison harness; reads one encoded VTXO as hex.
use std::io::{self, Read};

use ark::{ProtocolEncoding, Vtxo};
use bitcoin::hex::{DisplayHex, FromHex};
use bitcoin::{secp256k1::Secp256k1, Address, Network, OutPoint};

fn main() -> anyhow::Result<()> {
	let mut input = String::new();
	io::stdin().read_to_string(&mut input)?;
	let vtxo = Vtxo::deserialize(&Vec::<u8>::from_hex(input.trim())?)?;
	let script = Address::p2tr(&Secp256k1::verification_only(), vtxo.user_pubkey().x_only_public_key().0,
		None, Network::Bitcoin).script_pubkey();
	let path: Vec<_> = std::iter::once(vtxo.chain_anchor()).chain(vtxo.transactions()
		.map(|t| OutPoint::new(t.tx.compute_txid(), t.output_idx as u32)))
		.map(|outpoint| outpoint.to_string()).collect();
	println!("{}", serde_json::json!({
		"id": vtxo.id().to_string(), "amount_sat": vtxo.amount().to_sat(),
		"payout_script": script.as_bytes().to_lower_hex_string(), "path": path,
	}));
	Ok(())
}
