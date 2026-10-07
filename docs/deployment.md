# Deployment

## Postgres (captaind's database)

Postgres listens on localhost or a private network; only captaind and the sidecar connect. The sidecar uses captaind's DB role, or any role owning captaind's schema. Create its schema and tables once:

```sql
CREATE SCHEMA sidecar;
\i migrations/0001_sidecar.sql
```

At startup the sidecar:
- checks that its tables exist; it never creates them;
- takes `pg_try_advisory_lock`, so a second instance exits.

The connection has no TLS. Run the sidecar on the Postgres host, or reach Postgres over a private network.

## bitcoind (payout wallet)

- **Wallets.** Point the RPC URL at the loaded `payout` wallet (`/wallet/payout`). Other Core wallets may be loaded; captaind and watchmand use their own internal wallets.
- **Wallet loading.** Create it with `load_on_startup=true`. The sidecar checks it every tick.
- **Indexes.** `txindex=1`.
- **Float.** Keep it small; top it up from the watchman sweep address.
- **RPC.** The node's normal RPC credentials.

## Process

- Needs Postgres and bitcoind RPC only; no inbound ports. captaind's admin gRPC can stay bound to localhost.
- Run it under a supervisor with restart. It exits on Postgres connection loss and on invariant violations, and re-takes the lock on restart.
- captaind and watchmand also need a restart policy.

Logs default to `info` when `RUST_LOG` is unset or invalid. Set `RUST_LOG=error` to suppress warnings and summaries, or `RUST_LOG=abandon_ship_server=debug` for individual waiting reasons. Each finished tick attempt logs one summary: observed tip, candidates examined, claims, successful payout submissions (including retries), quarantines, elapsed milliseconds and success. A failed attempt may have no observed tip.

## Journal

`journal_path` is the payout record that survives a DB restore. Back it up separately from captaind's database. Never truncate it. New records contain all coin IDs of a batch and its raw transaction in one line. Complete legacy records remain readable; a partial final line is replaced on the next append.

## captaind upgrades

1. Audit every `UPDATE vtxo` / `spend_state` write in the new version. Each must be conditional and commit before releasing a signature, preimage or broadcast.
2. Run `tests/regtest/run-all.sh` against the new images.
3. Add the new schema version to `postgres.allowed_schema_versions`, then restart the sidecar.

The sidecar stops if the schema version changes under it.

## Restoring captaind's database

1. Stop captaind and the sidecar.
2. Restore the DB.
3. Run the sidecar with its independently retained journal. Before accessing Core, each tick validates journal history and transaction bytes, re-marks restored live coins as spent, reattaches restored claims, and checks the ledger. A completed pass writes `sidecar.reassert.completed_at`. Core or fee-estimate outages do not prevent this DB reconciliation; they can still prevent payment processing.
4. Gate every captaind start with `regtest/scripts/wait-reassert.sh`. Set the usual `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and password environment, plus `ABANDON_SHIP_JOURNAL` pointing to a readable copy of the same journal. The gate requires a completed reassertion newer than its own startup. Restored or previous-start markers cannot release it. Run the sidecar independently of captaind's health to avoid a startup dependency cycle.

The gate permits a fresh installation only when the journal exists and is empty, and both the VTXO and payout histories are empty. A missing journal stops it. `ABANDON_SHIP_SKIP_REASSERT_WAIT=1` explicitly bypasses the gate and logs a warning; ordinary manually ticked regtests use that override. Directly starting stock captaind without the gate does not enforce restore ordering. Waiting can include an in-progress tick, the polling interval and recovery work; there is no fixed one-tick deadline.

Missing or conflicting Ark history, including unfinished round participation, stops the sidecar before a new marker: restore the matching backup/WAL or repair the round offline first. A payment journal cannot reconstruct missing transfers. `--once` reports a failed payment tick with a nonzero exit even when DB reassertion completed; use the fresh marker to distinguish those results.

Restored claimed rows sharing a transaction reattach its original bytes in one database transaction. Missing transaction bytes stop recovery with the coin and transaction IDs; recover those bytes from the payout database or independent journal backup. Transactions whose payout rows are absent stay in the journal retry queue. Temporary broadcast rejection retains their funding inputs and does not remove the obligation.

## Client fee receipts

Publish only `journal_path` with its extension replaced by `.receipts`, at
`/expiry-payouts/<txid>.json` on the Ark HTTP origin. Never serve the adjacent
journal. Each JSON file contains `txid` and `outputs` entries with `vout`,
`amount_sat` (net) and `fee_sat` (original payout mining-fee deduction).
Coins sharing a key share an output and combined deduction; change is omitted.
The client’s later spend has its own separate mining fee.

Files are atomically renamed and fsynced. Publication errors warn without
blocking payment. Pending payment retries recreate missing files. To rebuild
confirmed files, stop the sidecar and run
`abandon-ship-server config.toml --export-receipts`, then restart it. Export
reads the DB, journal and Core, holds the leader lock, and writes receipt files
without reconciling or altering the payment ledger. It attempts all known
transactions and exits nonzero if any receipt cannot be reconstructed.
Keep payout metadata when restoring: missing or incomplete entitlement records
can prevent exact fee reconstruction even while the payment remains recoverable.
Missing fee metadata must appear as unknown in the client, never as zero.
