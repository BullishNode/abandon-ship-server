# abandon-ship-server

Pays the value of expired, unrefreshed Ark coins on-chain to BIP86 `tr(coin_pubkey)`, at most once, for a [captaind](https://gitlab.com/ark-bitcoin/bark) server (bark master `6768e0fb4`). Runs next to captaind without modifying it: it reads captaind's Postgres, changes a coin's `spend_state` and `banned_until_height` only through conditional updates, and keeps its own state in a `sidecar` schema and a local journal file.

## Flow (one tick)

1. **Checks.**
   - captaind schema version is allowlisted;
   - the payout wallet is loaded;
   - `estimatesmartfee` returns a rate. Without one, nothing is claimed or paid.
2. **Journal reconciliation.**
   - every paid ledger row is in the journal;
   - a journaled coin that is spendable again in captaind (DB restore) is set back to spent and quarantined.
3. **Settle.** Rebroadcast stored payout txs that are unconfirmed or evicted, and mark txs with 6 confirmations as confirmed.
4. **Select.** `pubkey` coins in state `spendable` or `unclaimed`, past `expiry + grace_blocks`, not paid, not quarantined. If unpaid claims exist, pay those first and claim nothing new.
5. **Per coin.** A problem with one coin quarantines that coin; it never stops the loop.
   1. **Amount.** Skip if below `min_payout_sat`, or if its fee share would exceed `max_fee_pct_per_payout` or leave less than 330 sat.
   2. **Chain validation.** Decode the stored VTXO and run `Vtxo::validate(&anchor_tx)`. Amount, key and expiry come from the validated VTXO, never from DB columns. It must be a `Pubkey`-policy coin of `server_pubkey`.
   3. **Unclaimed outputs.** Coins whose last hArk step is unsigned (owner not back yet) are accepted only if:
      - validation fails at that last step alone;
      - `validate_unsigned` passes;
      - the output it spends, which the signed parent creates, equals `HarkLeafVtxoPolicy{coin key, unlock_hash}.taproot()`.
   4. **Sweep.** The anchor (round funding output) must be spent by a tx paying only the configured `sweep_addresses` (ignoring OP_RETURN and P2A), buried `sweep_min_confs`. A tree tx spending it means the round was partially unrolled: quarantine.
   5. **In flight.** Skip while a round participation still references the coin.
   6. **Ban, then wait.** Set `banned_until_height`, then wait `ban_wait_secs`. The claim needs that exact ban still in place: if an operator lifts it, the wait restarts.
   7. **Claim.** In one transaction, serialised per round:
      - `UPDATE vtxo SET spend_state='spent' WHERE … spend_state IN ('spendable','unclaimed') AND <our ban>`;
      - check that the DB amount equals the validated amount;
      - check that the round's payouts stay at or below the funding output;
      - insert the payout row.
6. **Pay.** For all claimed coins, in one batch:
   - re-derive each row's address and amount from the chain-validated VTXO;
   - one output per address, fee subtracted from the outputs, funded at the checked rate;
   - verify the tx: exact outputs, at most one change output owned by the wallet, per-output fee share;
   - store it in the DB, then append it to the journal (fsync), then broadcast.
7. **Invariants.** Every paid coin is `spent` with no round, arkoor or offboard spend recorded, and no paid coin row is missing. A violation exits the process.

## Why a coin cannot be paid twice

| Second redemption | Prevented by |
| --- | --- |
| Refresh, offboard, arkoor or Lightning in Ark | captaind commits every spend with a conditional `spend_state` update before releasing a signature, preimage or broadcast (`tree.rs`, `forfeit.rs`, `arkoor.rs`, `offboards.rs`); the claim uses the same condition |
| Unilateral exit | payout only after the anchor is swept wholesale, `sweep_min_confs` deep |
| Ledger edits, resets, DB restore | local journal, pay-time re-derivation, invariant check |
| Retries, crashes | `payout.vtxo_id` is unique; tx stored before broadcast; rebroadcast reuses it |

## Config

See `config.example.toml`. The keys:

| Key | Meaning |
| --- | --- |
| `server_pubkey` | captaind's server key |
| `journal_path` | local payout journal |
| `postgres.conninfo` | captaind's database |
| `postgres.allowed_schema_versions` | allowlisted captaind schema versions |
| `bitcoind.url` | the payout wallet |
| `sweep_addresses` | where the watchman sweeps |
| `grace_blocks` | wait after expiry before paying |
| `sweep_min_confs` | required sweep depth |
| `ban_blocks` | length of the ban the sidecar sets |
| `ban_wait_secs` | wait after banning, before claiming |
| `max_batch` | coins per payout batch |
| `payout_conf_target` | confirmation target for the fee estimate |
| `max_fee_pct_per_payout` | maximum fee share per coin |
| `min_payout_sat` | smallest coin paid on-chain |
| `max_quarantine_per_tick` | circuit breaker |

On mainnet, `sweep_min_confs ≥ 100` and `grace_blocks ≥ 144`.

## Run

```sh
cargo run --release -- config.toml           # loop
cargo run --release -- config.toml --once    # one tick
RUST_LOG=abandon_ship_server=debug ...       # logs why each coin waits
```

Tables are created by the DB admin (`migrations/0001_sidecar.sql`). Setup and runbooks: `docs/deployment.md`. Design and threats: `docs/design.md`.

## Layout

| Path | Contents |
| --- | --- |
| `src/main.rs` | tick loop, per-coin decisions, payout |
| `src/db.rs` | Postgres queries (reads captaind; conditional writes) |
| `src/chain.rs` | bitcoind JSON-RPC (untyped) |
| `src/checks.rs` | fee rule, sweep detection, payout verification (pure, unit-tested) |
| `src/journal.rs` | append-only payout journal |
| `src/payout.rs` | BIP86 payout address |
| `examples/coin_key_descriptor.rs` | prints `tr(xprv/350'/0'/*)` from a Bark mnemonic, to spend payouts without Bark |
| `regtest/` | docker stack and helper scripts |
| `tests/regtest/` | end-to-end scenarios |
| `tests/*.md` | edge-case catalogue |
