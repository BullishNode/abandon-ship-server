//! Payout destination: BIP86 `tr(coin_pubkey)` (key-path, no script tree).

use bitcoin::secp256k1::{PublicKey, Secp256k1};
use bitcoin::{Address, Network};

pub fn address_for_pubkey(pk: &PublicKey, network: Network) -> Address {
	let secp = Secp256k1::verification_only();
	Address::p2tr(&secp, pk.x_only_public_key().0, None, network)
}

#[cfg(test)]
mod tests {
	use super::*;
	use bitcoin::secp256k1::SecretKey;

	#[test]
	fn pays_bip86_taproot_of_coin_key() {
		let secp = Secp256k1::new();
		let pk = PublicKey::from_secret_key(&secp, &SecretKey::from_slice(&[7u8; 32]).unwrap());
		let addr = address_for_pubkey(&pk, Network::Regtest);
		let expected = Address::p2tr(&secp, pk.x_only_public_key().0, None, Network::Regtest);
		assert_eq!(addr, expected);
		assert!(addr.to_string().starts_with("bcrt1p"));
	}
}
