# Deployment: least privilege (threat model T5, T6)

## Postgres role (captaind's database)

Run as the database owner. The sidecar gets read access to what it reads, column-level `UPDATE` on `vtxo` for the claim and the ban, and its own schema. Nothing else.

```sql
CREATE ROLE sidecar LOGIN PASSWORD '<secret>';

-- the sidecar's own schema, pre-created so the role needs no CREATE on the DB
CREATE SCHEMA sidecar AUTHORIZATION sidecar;

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
