use std::path::Path;

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
	pub network: bitcoin::Network,
	/// captaind's server pubkey (hex). Coins signed for any other server are refused.
	pub server_pubkey: bitcoin::secp256k1::PublicKey,
	/// Append-only payout journal on local disk (survives DB restores).
	pub journal_path: std::path::PathBuf,
	pub poll_interval_secs: u64,
	pub postgres: Postgres,
	pub bitcoind: Bitcoind,
	pub policy: Policy,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Postgres {
	pub conninfo: String,
	pub allowed_schema_versions: Vec<i32>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Bitcoind {
	pub url: String,
	pub user: String,
	pub pass: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Policy {
	pub sweep_addresses: Vec<String>,
	pub grace_blocks: u32,
	pub sweep_min_confs: u32,
	pub ban_blocks: u32,
	pub ban_wait_secs: u64,
	pub max_batch: i64,
	pub payout_conf_target: u16,
	/// Pay a coin on-chain only if its fee share is at most this % of its value.
	pub max_fee_pct_per_payout: u64,
	/// Never pay a coin on-chain below this amount (it stays refreshable).
	pub min_payout_sat: u64,
	/// Stop the process if more coins than this are quarantined in one tick
	/// (likely an encoding or schema change, not bad coins).
	pub max_quarantine_per_tick: u64,
}

impl Config {
	pub fn load(path: &Path) -> anyhow::Result<Config> {
		let s = std::fs::read_to_string(path)?;
		let cfg: Config = toml::from_str(&s)?;
		anyhow::ensure!(!cfg.policy.sweep_addresses.is_empty(), "policy.sweep_addresses is empty");
		anyhow::ensure!(!cfg.postgres.allowed_schema_versions.is_empty(), "no allowed captaind schema versions");
		anyhow::ensure!((1..=99).contains(&cfg.policy.max_fee_pct_per_payout), "max_fee_pct_per_payout must be 1..=99");
		anyhow::ensure!(cfg.policy.min_payout_sat >= 330, "min_payout_sat must be >= 330 (dust)");
		// Floors that only regtest/signet may go below.
		if cfg.network == bitcoin::Network::Bitcoin {
			anyhow::ensure!(cfg.policy.sweep_min_confs >= 100, "sweep_min_confs must be >= 100 on mainnet");
			anyhow::ensure!(cfg.policy.grace_blocks >= 144, "grace_blocks must be >= 144 on mainnet");
		}
		anyhow::ensure!(cfg.policy.sweep_min_confs >= 1, "sweep_min_confs must be >= 1");
		anyhow::ensure!(cfg.policy.ban_blocks >= 1 && cfg.policy.ban_blocks <= 10_000, "ban_blocks must be 1..=10000");
		anyhow::ensure!(cfg.policy.max_batch >= 1 && cfg.policy.max_batch <= 500, "max_batch must be 1..=500");
		Ok(cfg)
	}
}
