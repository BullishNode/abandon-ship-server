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

- **Wallets.** `payout` must be the only wallet on this node. captaind and watchmand use their own internal wallets.
- **Wallet loading.** Create it with `load_on_startup=true`. The sidecar checks it every tick.
- **Indexes.** `txindex=1`.
- **Float.** Keep it small; top it up from the watchman sweep address.
- **RPC.** The node's normal RPC credentials.

## Process

- Needs Postgres and bitcoind RPC only; no inbound ports. captaind's admin gRPC can stay bound to localhost.
- Run it under a supervisor with restart. It exits on Postgres connection loss and on invariant violations, and re-takes the lock on restart.
- captaind and watchmand also need a restart policy.

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
3. Start the sidecar first, with its journal. On its first tick it re-marks journaled coins that are spendable again as spent, quarantines them, and rebroadcasts their journaled payout tx.
4. Start captaind only after successful reconciliation. Missing or conflicting Ark history, including unfinished round participation, stops the sidecar: restore the matching backup/WAL or repair the round offline first. A failed `--once` run exits nonzero and must not authorize starting captaind. A payment journal cannot reconstruct missing transfers.

Restored claimed rows reattach their original journaled transaction. Transactions whose payout rows are absent stay in the journal retry queue. Temporary broadcast rejection retains their funding inputs and does not remove the obligation.
