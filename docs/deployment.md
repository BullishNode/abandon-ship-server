# Deployment: least privilege (threat model T5, T6)

## Postgres role (captaind's database)

Run as the database owner. The sidecar gets read access to what it reads, column-level `UPDATE` on `vtxo` for the claim and the ban, and its own schema. Nothing else.

```sql
CREATE ROLE sidecar LOGIN PASSWORD '<secret>';

-- the sidecar's schema and tables are owned by the admin; the role gets
-- SELECT/INSERT/UPDATE only (no DELETE/TRUNCATE on its own ledger)
CREATE SCHEMA sidecar;
\i migrations/0001_sidecar.sql
GRANT USAGE ON SCHEMA sidecar TO sidecar;
GRANT SELECT, INSERT, UPDATE ON sidecar.ban, sidecar.quarantine, sidecar.payout TO sidecar;

GRANT SELECT ON vtxo, round_part_input, refinery_schema_history TO sidecar;
-- column-level: participation state, plus the unlock preimage needed to
-- validate unclaimed hArk outputs (A1). A preimage alone cannot spend anything.
GRANT SELECT (id, unlock_hash, unlock_preimage, round_id, forfeited_at) ON round_participation TO sidecar;
GRANT UPDATE (spend_state, banned_until_height, updated_at) ON vtxo TO sidecar;

-- captaind's vtxo update trigger copies the old row into vtxo_history, and the
-- trigger runs with the caller's rights.
GRANT INSERT ON vtxo_history TO sidecar;
```

To verify on regtest:
- whether `vtxo_history` has its own sequence that also needs `USAGE`;
- the refinery table name (`refinery_schema_history` assumed).

The sidecar never creates tables. At startup it checks they exist, and refuses to run if they don't.

Leader lock: the sidecar holds `pg_try_advisory_lock` for its session. A second instance exits at startup.

## bitcoind (payout wallet)

- **Wallets.** captaind and watchmand keep their own internal wallets, not bitcoind wallets. The only wallet on this bitcoind should be `payout`. `rpcwhitelist` limits methods, not wallets, so do not add other wallets to this node.
- **Wallet loading.** Create the wallet with `load_on_startup=true` (`createwallet payout … true` or `loadwallet payout true`). bitcoind does not reload wallets after a restart otherwise. The sidecar checks it every tick and errors loudly.
- **Float.** Keep a small float in `payout`. Top it up from the watchman sweep address as payouts accrue.
- **Indexes.** `txindex=1` is needed for `getrawtransaction` on anchors and sweep txs.
- **RPC user.** Restricted to the calls the sidecar makes:

```ini
rpcauth=sidecar:<salt$hash>
rpcwhitelistdefault=0
rpcwhitelist=sidecar:getwalletinfo,getblockcount,getrawtransaction,gettxout,getaddressinfo,walletcreatefundedpsbt,walletprocesspsbt,finalizepsbt,sendrawtransaction
```

## Network

- The sidecar needs Postgres and bitcoind RPC only. It never talks to captaind's admin gRPC (T4), so keep that bound to localhost.
- Run the sidecar on its own host or container, with no inbound ports.

## Upgrades (T10)

`postgres.allowed_schema_versions` lists the captaind schema versions the upgrade gate has signed off. After upgrading captaind:
1. rerun the upgrade gate: the spend-path audit and the race suite;
2. add the new version;
3. restart the sidecar.

The sidecar refuses to start on any other version.

## Postgres connection

The client connects without TLS. Run the sidecar on the Postgres host, or reach Postgres over a private network or tunnel. Do not expose Postgres to a public network for it.

## Journal

`journal_path` is the payout record that survives a DB restore. Back it up **separately** from captaind's database and never truncate it.

## Runbook: restoring captaind's database

1. Stop captaind **and** the sidecar.
2. Restore the DB.
3. Start the **sidecar first**, with its journal intact. On its first tick, it re-marks every journaled coin that is spendable again as spent, and quarantines it.
4. Then start captaind.

Starting captaind first leaves a window in which a user could refresh a coin that was already paid on-chain.
