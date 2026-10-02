# Observations from regtest testing (for deciding on architecture changes)

Stack: captaind/bark nightly-2026-10-01 (master 6768e0fb4), Bitcoin Core 31.0, `regtest/` in this repo. Each entry is something seen on regtest, with what it means for the architecture. **AC?** marks a candidate for a profound architecture change.

## Confirmed design assumptions
- **O1.** captaind still accepts expired coins for refresh before the sweep (A1) **and after the sweep** (A2: refresh and offboard). Honour-on-return works with no code change.
- **O2.** captaind records the wholesale sweep on the funding-output row (`onchain_spent_txid`). The sweep tx pays only `sweep_address`, and one sweep spends several rounds' outputs at once.
- **O3.** `Vtxo::validate(&anchor_tx)` passes for coins as captaind stores them: round leaves, arkoor outputs and board coins. Chain validation of the DB blob is viable.
- **O4.** A ban (admin RPC or the DB column) blocks refresh and offboard, and lapses on time (A5).
- **O5.** The paid coin is refused by captaind afterwards (S4). A seed restore skips it: "6 skipped" (S5).
- **O6.** One user exit makes the round's funding output spend to a tree tx. Every other coin in that round can no longer be paid automatically and goes to manual review (#12). **AC?** At scale, one exit per round takes the whole round out of automatic payouts. A partial-unroll-aware payout path (pay coins whose own branch is still swept) would remove that, at the cost of the complex proof we dropped for simplicity.

## Bugs found and fixed
- **O7.** Coins sent to the same Ark address share a key. The payout keyed outputs by address in a HashMap, so amounts collided (#36). Fixed: one output per address, carrying the sum.
- **O8.** One unavailable anchor or spender tx aborted the whole tick, and any tick error killed the process (#38). Fixed: per-coin wait/quarantine; tick errors are retried.
- **O9.** A wrong `sweep_addresses` config would have quarantined every coin permanently (#28). Fixed: a non-sweep spender is quarantined only if it is a known tree tx; otherwise wait and warn.
- **O10.** (Seen twice: also `getwalletinfo` lost its `balance` field in Core 31.) `bitcoincore-rpc` 0.19 cannot decode Core 31's `anchor` (P2A) script type, and every Ark tree tx has one. Fixed: an untyped RPC call plus our own decoding. **AC?** The typed RPC client is a liability across Core upgrades. Consider dropping `bitcoincore-rpc` for a minimal untyped client.
- **O11.** `CREATE SCHEMA IF NOT EXISTS` needs CREATE on the database, even when the schema exists. Fixed: the admin pre-creates the schema.

## Open risks (not yet fixed)
- **O12.** **DB restore / ledger loss can double-pay (#109/#110).** If captaind's DB is restored from an older backup, paid coins become `spendable` again while their payout txs are on-chain. **AC?** Needs a payout record that survives a DB restore. Options:
  - an append-only journal on the sidecar host;
  - checking the payout wallet's own history before claiming;
  - a coin-id commitment in each payout tx (OP_RETURN).
- **O13.** The client does not learn a coin was paid out. The stock wallet keeps showing it as `needs_refresh` (S4). The client work (variant B) is required, not optional.
- **O14.** No RBF bump yet (#33). A stuck low-fee payout stays stuck.
- **O15.** (Near miss seen: a 400-sat coin paid out 333 sat, 3 above dust. Fixed with a config floor, `min_payout_sat ≥ 330 + 150 × max_fee_rate`; at a 50 sat/vB cap that is about 7.8k sat, so smaller coins only come back by refresh.) The payout batch is all-or-nothing. One bad output (below dust after fees, #95) blocks every payout in the batch. **AC?** Smaller batches, or retry without the offending output.
- **O16.** **captaind (and watchmand) panic when the chain tip height goes backward:** "bitcoind chain went backward…" (`server/src/sync/block_index.rs:127`). Triggered on regtest by `invalidateblock`. A height-lowering reorg is rare on mainnet but possible. Upstream robustness issue for Second. For us:
  - run both with an auto-restart policy;
  - the sidecar must not depend on captaind being up. It does not: it reads the DB and bitcoind only.
- **O17.** **bitcoind restart unloads the payout wallet.** Wallets created without `load_on_startup` are not reloaded. The sidecar only noticed when a payout was due. Fixed: a `getwalletinfo` check every tick, and the deployment doc requires `load_on_startup`.
- **O18.** **On Postgres connection loss the sidecar exits** (the I2 check fails on the closed connection). This is good: it never spins without its advisory lock. Requires a supervisor (systemd/docker restart policy) for the sidecar.
- **O19.** **A user returning during the ban-wait window cannot refresh** ("unusable inputs") and is paid on-chain instead (#56). Acceptable, but the wallet should explain it.
- **O20.** **H2 is real and crashes all of captaind,** not just the round. Seen when a coin flips to spent inside a round's ~5s submit window, after the user submitted and before captaind persists:
  - "failed to store finished and signed round";
  - "Fatal round error … failed to find bad vtxo … unexpected number of rows";
  - "critical worker stopped" → process exit, then restart.

  No funds at risk: persist comes before the funding broadcast. Interactive participations are **not** visible in `round_part_input` before persist, so the sidecar cannot see them. Defences:
  - captaind refuses submissions of banned coins;
  - `ban_wait` exceeds a round's duration;
  - (fixed after #57) the claim requires **our** ban to still be in place. An operator unban during the wait restarts the wait instead of letting a claim race a fresh submission.

  Upstream issue for Second: a persist failure should fail the round, not the process. **AC?** This is the strongest argument for the upstream `SettleVtxo` RPC, which would take captaind's in-memory `vtxos_in_flux` lock and make H2 impossible.
- **O21.** captaind's restart policy matters: H2 and O16 both end in a process exit. Without `restart: unless-stopped`, the Ark server stays down.
- **O22.** **The stock CLI `bark refresh --all` fails entirely if one input is a paid-out (spent) coin.** The healthy coins in the same wallet are then not refreshed either. Seen on u4: "unusable inputs: [24ce…]". The maintenance path retries without rejected inputs (`lib.rs:1856-1910`); the explicit refresh path does not. Client-side (variant B) must drop paid coins *before* any refresh. Otherwise one payout can cause the user's other coins to expire.
- **O23.** **Fee estimation offline or broken strands claimed coins.** With bitcoind's estimator returning "Insufficient data" and no `-fallbackfee` (the production default), a coin was claimed (spent in captaind, so it cannot be refreshed) and then the payout failed: "Fee estimation failed. Fallbackfee is disabled". Fixed:
  - **fee gate:** no claims unless `estimatesmartfee` gives a rate within `max_fee_rate_sat_vb`;
  - the payout is funded at exactly that checked rate (explicit `fee_rate`, so the wallet never falls back on its own);
  - no fallback rate of any kind (owner ruling); regtest gets a real estimator by seeding it with fee-paying txs (`regtest/seed-fees`).

  Verified: no estimate → "not claiming"; with a rate → the stranded coin was paid.
- **O24.** (Superseded by O27.) **A1 confirmed.** Delegated-refresh outputs whose owner never returns stay `unclaimed`; earlier the sidecar ignored them, so they were never paid. Now they are candidates:
  - the unlock preimage comes from `round_participation` (hex TEXT);
  - the tree part validates;
  - the final hash-locked leaf carries **no signature** until the owner returns ("missing signature").

  So they cannot be fully verified, and they go to manual review with an explicit reason. An unverified automatic payout would reopen T1. **AC?** Upstream ask to Second: a validation that verifies everything except the unsigned final hArk step. Then A1 coins can be auto-paid.
- **O25.** **A panic from external data:** `row.get` on `unlock_preimage` (TEXT, not BYTEA) crashed the process. Fixed: every DB read is `try_get`, and no `unwrap`/`expect` remains on external data.
- **O26.** Owner ruling (2026-10-02): no feerate cap. The fee rule is a per-coin percentage (`max_fee_pct_per_payout`) plus a minimum amount (`min_payout_sat`). The percentage bounds what any coin loses to fees even with an absurd estimate.
- **O27.** **A1 resolved without upstream.** In an unclaimed hArk output, the key and the amount are committed by the signed parent: the spent output's script is built from `MuSig(user, server)` + the unlock hash. Only the final leaf transition is unsigned. The rule:
  - full validation fails only at the last v1 hash-locked step;
  - `validate_unsigned` passes;
  - the spent output's script equals `HarkLeafVtxoPolicy{coin key, unlock_hash}.taproot()`;
  - the amount is at most that output's value.

  Verified: the 8.4M-sat unclaimed coin was paid, and the payout is spendable with the owner's seed (coin-key descriptor). v0 hArk coins still go to quarantine (no public v0 policy type).
