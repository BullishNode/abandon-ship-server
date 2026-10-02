//! Recovery helper: reads a Bark wallet mnemonic on stdin and prints the
//! private descriptor of its coin keys, `tr(<xprv>/350'/0'/*)`, so payouts
//! to `tr(coin_pubkey)` can be swept with any descriptor wallet.
//! Usage: echo "<mnemonic>" | cargo run --example coin_key_descriptor -- regtest

use std::io::Read;
use std::str::FromStr;

use bitcoin::bip32::{DerivationPath, Xpriv};
use bitcoin::secp256k1::Secp256k1;

fn main() -> anyhow::Result<()> {
	let network = bitcoin::Network::from_str(&std::env::args().nth(1).unwrap_or("bitcoin".into()))?;
	let mut words = String::new();
	std::io::stdin().read_to_string(&mut words)?;
	let seed = bip39::Mnemonic::parse(words.trim())?.to_seed("");
	let secp = Secp256k1::new();
	let master = Xpriv::new_master(network, &seed)?;
	let path = DerivationPath::from_str("m/350'/0'")?;
	let xprv = master.derive_priv(&secp, &path)?;
	println!("tr({xprv}/*)");
	Ok(())
}
