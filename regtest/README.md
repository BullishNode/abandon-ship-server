# regtest

Docker stack for testing: `bitcoind` (Core 31, no `-fallbackfee`), `postgres`, `cln` (hold plugin), `captaind` and `watchmand` (bark nightly-2026-10-01 = master `6768e0fb4`), plus a `bark` CLI service for wallets. Coin lifetime is 64 blocks, exit delay 12 blocks and sweep interval 30 s. All credentials are test-only.

## Setup

1. `docker compose up -d bitcoind postgres`.
2. Create the bitcoind wallets `faucet`, `payout` and `sweep`, each with `load_on_startup`.
3. Mine 150 blocks and fund `payout`.
4. Render `watchmand.toml` from `watchmand.toml.tmpl`, using a `sweep` wallet address (bech32m).
5. `docker compose up -d cln captaind watchmand`.
6. Fund the captaind rounds wallet (`./captaind rpc wallet`) and the watchman wallet (its descriptor is in `wallet_changeset`).
7. Create the `sidecar` schema and tables as in `docs/deployment.md`.
8. Write `sidecar.toml` from `config.example.toml`.
9. Run `./seed-fees` so that `estimatesmartfee` has data.

## Helpers

| Script | Use |
| --- | --- |
| `./btc …` | `bitcoin-cli` |
| `./bark <wallet> …` | Bark CLI with datadir `/wallets/<wallet>` |
| `./psql …` | captaind's DB as the superuser |
| `./captaind …` | captaind CLI (`rpc wallet`, `rpc ban …`) |
| `./coins` | user coins as captaind sees them |
| `./mineto <height>` | mine to a height |
| `./seed-fees [blocks]` | fee-paying txs, so the estimator has data |

`sidecar.toml`, `watchmand.toml` and `payouts.journal` are git-ignored.

`tests/regtest/run-all.sh` runs the scenarios. The harness waits for captaind's recorded tip after mining (failing after ten minutes without progress) and tops up its rounds wallet from the faucet by 20 BTC when its trusted balance falls below 2 BTC. Scenario stdout and stderr are saved under `$OUT` (default `/tmp/abandon-regtest`).
