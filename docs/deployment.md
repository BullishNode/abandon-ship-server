# Deployment

## Postgres (captaind's database)

Run as the database owner. The admin owns the `sidecar` schema and tables. The `sidecar` role can read what it needs, update three `vtxo` columns, and write its own tables without `DELETE`.

```sql
CREATE ROLE sidecar LOGIN PASSWORD '<secret>';

CREATE SCHEMA sidecar;
\i migrations/0001_sidecar.sql
GRANT USAGE ON SCHEMA sidecar TO sidecar;
GRANT SELECT, INSERT, UPDATE ON sidecar.ban, sidecar.quarantine, sidecar.payout TO sidecar;

GRANT SELECT ON vtxo, round_part_input, refinery_schema_history TO sidecar;
-- unlock_preimage is needed to validate unclaimed hArk outputs; it cannot spend anything alone
GRANT SELECT (id, unlock_hash, unlock_preimage, round_id, forfeited_at) ON round_participation TO sidecar;
GRANT UPDATE (spend_state, banned_until_height, updated_at) ON vtxo TO sidecar;
-- captaind's vtxo update trigger writes vtxo_history with the caller's rights
GRANT INSERT ON vtxo_history TO sidecar;
```

At startup the sidecar:
- checks that its tables exist; it never creates them;
- takes `pg_try_advisory_lock`, so a second instance exits.

The connection has no TLS. Run the sidecar on the Postgres host, or reach Postgres over a private network.

## bitcoind (payout wallet)

- **Wallets.** `payout` must be the only wallet on this node: `rpcwhitelist` restricts methods, not wallets. captaind and watchmand use their own internal wallets.
- **Wallet loading.** Create it with `load_on_startup=true`. The sidecar checks it every tick.
- **Indexes.** `txindex=1`.
- **Float.** Keep it small; top it up from the watchman sweep address.
- **RPC user:**

```ini
rpcauth=sidecar:<salt$hash>
rpcwhitelistdefault=0
rpcwhitelist=sidecar:getwalletinfo,getblockcount,estimatesmartfee,getrawtransaction,gettxout,getaddressinfo,walletcreatefundedpsbt,walletprocesspsbt,finalizepsbt,sendrawtransaction
```

## Process

- Needs Postgres and bitcoind RPC only; no inbound ports. captaind's admin gRPC can stay bound to localhost.
- Run it under a supervisor with restart. It exits on Postgres connection loss and on invariant violations, and re-takes the lock on restart.
- captaind and watchmand also need a restart policy.

## Journal

`journal_path` is the payout record that survives a DB restore. Back it up separately from captaind's database. Never truncate it.

## captaind upgrades

1. Audit every `UPDATE vtxo` / `spend_state` write in the new version. Each must be conditional and commit before releasing a signature, preimage or broadcast.
2. Run `tests/regtest/run-all.sh` against the new images.
3. Add the new schema version to `postgres.allowed_schema_versions`, then restart the sidecar.

The sidecar stops if the schema version changes under it.

## Restoring captaind's database

1. Stop captaind and the sidecar.
2. Restore the DB.
3. Start the sidecar first, with its journal. On its first tick it re-marks journaled coins that are spendable again as spent, quarantines them, and rebroadcasts their journaled payout tx.
4. Start captaind.
