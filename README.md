# abandon-ship-server

Pays the value of expired, unrefreshed Ark coins on-chain to BIP86 `tr(coin_pubkey)`, at most once, for a [captaind](https://gitlab.com/ark-bitcoin/bark) server (bark master `6768e0fb4`). Runs next to captaind without modifying it: it reads captaind's Postgres, changes a coin's `spend_state` and `banned_until_height` only through conditional updates, and keeps its own state in a `sidecar` schema and a local journal file.

Scope (`docs/design.md`): captaind, its DB, bitcoind and the sidecar are run and trusted by one operator; the sidecar defends against users refreshing, exiting and being paid for the same coin, crashes and DB restores, and fees or selection stalling payouts.

## Flow (one tick)

1. **Checks.**
   - captaind schema version is allowlisted;
   - the payout wallet is loaded;
   - `estimatesmartfee` returns a rate. Without one, nothing is claimed or paid.
2. **Journal reconciliation.**
   - every paid ledger row is in the journal;
   - a journaled coin that is spendable again in captaind (DB restore) is set back to spent, quarantined, and its journaled payout tx is broadcast again (a no-op if already known).
3. **Settle.** Rebroadcast stored payout txs that are unconfirmed or evicted, and mark txs with 6 confirmations as confirmed.
4. **Select.** `pubkey` coins in state `spendable` or `unclaimed`, past `expiry + grace_blocks`, of at least `min_payout_sat`, not paid, not quarantined. Claimed coins still payable at the current rate count against `max_batch`; ones that fees made unaffordable wait without blocking new claims.
5. **Per coin.** A problem with one coin quarantines that coin; it never stops the loop.
   1. **Decode.** Amount, key and anchor come from the stored VTXO. An undecodable one is quarantined.
   2. **Fee share.** Skip if its fee share would exceed `max_fee_pct_per_payout` or leave less than 330 sat.
   3. **Sweep.** The anchor (round funding output) must be spent by a tx paying only the configured `sweep_addresses` (ignoring OP_RETURN and P2A), buried `sweep_min_confs`. A tree tx spending it means the round was partially unrolled: quarantine.
   4. **In flight.** Skip while a round participation still references the coin.
   5. **Ban, then wait.** Set `banned_until_height`, then wait `ban_wait_secs`. The claim needs that exact ban still in place: if an operator lifts it, the wait restarts.
   6. **Claim.** In one transaction: `UPDATE vtxo SET spend_state='spent' WHERE … spend_state IN ('spendable','unclaimed') AND <our ban>`, then insert the payout row.
6. **Pay.** For all claimed coins, in one batch:
   - one output per address, fee subtracted from the outputs, funded at the checked rate;
   - verify the tx: exact outputs, at most one change output owned by the wallet, per-output fee share;
   - store it in the DB, then append it to the journal (fsync), then broadcast.
7. **Invariants.** Every paid coin is `spent` with no round, arkoor or offboard spend recorded, and no paid coin row is missing. A violation exits the process.

## Why a coin cannot be paid twice

| Second redemption | Prevented by |
| --- | --- |
| Refresh, offboard, arkoor or Lightning in Ark | captaind commits every spend with a conditional `spend_state` update before releasing a signature, preimage or broadcast (`tree.rs`, `forfeit.rs`, `arkoor.rs`, `offboards.rs`); the claim uses the same condition |
| Unilateral exit | payout only after the anchor is swept wholesale, `sweep_min_confs` deep |
| DB restore | local journal (with the raw tx), invariant check |
| Retries, crashes | `payout.vtxo_id` is unique; tx stored before broadcast; rebroadcast reuses it |

## Config

`config.example.toml`, with a comment per key. On mainnet, `sweep_min_confs ≥ 100` and `grace_blocks ≥ 144`.

## Run

```sh
cargo run --release -- config.toml           # loop
cargo run --release -- config.toml --once    # one tick
RUST_LOG=abandon_ship_server=debug ...       # logs why each coin waits
```

Tables: apply `migrations/0001_sidecar.sql` once. Setup and runbooks: `docs/deployment.md`. Scope, failures and guards: `docs/design.md`.

## Layout

| Path | Contents |
| --- | --- |
| `src/main.rs` | tick loop, per-coin decisions, payout |
| `src/db.rs` | Postgres queries (reads captaind; conditional writes) |
| `src/chain.rs` | bitcoind JSON-RPC (untyped) |
| `src/checks.rs` | fee rule, sweep detection, payout verification (pure, unit-tested) |
| `src/journal.rs` | append-only payout journal |
| `examples/coin_key_descriptor.rs` | prints `tr(xprv/350'/0'/*)` from a Bark mnemonic, to spend payouts without Bark |
| `regtest/` | docker stack and helper scripts |
| `tests/regtest/` | end-to-end scenarios |
| `tests/*.md` | edge-case catalogue |
