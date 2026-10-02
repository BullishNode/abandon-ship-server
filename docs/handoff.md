# Hand-off: expired-coin sidecar, tested with bark-web

As of 2026-10-02. We built a sidecar that pays expired, unrefreshed Ark coins on-chain to their owner's own key. Then we tested it end to end on regtest with bark-web, to see the flow from the operator's side and from the user's side.

## The problem

An Ark coin (VTXO) expires unless its wallet refreshes it into a new round before the round's expiry height. At expiry, captaind's watchman sweeps the round's on-chain funding output back to the operator. From then on, the owner can no longer exit unilaterally.

- **Stock captaind still honours expired coins.** A returning owner can still refresh or offboard after the sweep, as long as the operator is still running and keeps the records. We verified this on regtest.
- **An owner who never returns is not covered.** Someone who loses their device, dies or forgets the wallet leaves the operator holding the money indefinitely.
- **The sidecar settles that case.** After a grace period, it pays each expired, unrefreshed coin on-chain to BIP86 `tr(coin_pubkey)`, at most once, with no change to captaind. The owner recovers the payout from their seed, using the descriptor `tr(xprv/350'/0'/*)` ([`examples/coin_key_descriptor.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/examples/coin_key_descriptor.rs)).

A user who returns within the grace period simply refreshes, and the sidecar never touches the coin.

## How it works

```
Operator:  coin expires ─▶ watchman sweeps round ─▶ grace period ─▶ sidecar pays (ban, claim, batch tx)
                                                       │                         │ payout to tr(coin key)
                                                       ▼ owner back in time      ▼
User:                             owner refreshes, never paid    Paying out ─▶ Paid out ─▶ moved on-chain
```

**Operator side, one sidecar tick:**
1. **Checks:** the schema version is allowlisted, the payout wallet is loaded, and `estimatesmartfee` returns a rate. With no estimate, nothing happens.
2. **Journal reconciliation**, then rebroadcast of stored payouts.
3. **Candidates:** expired coins past `grace_blocks`, not paid, not quarantined, and affordable under the per-coin fee rule.
4. **Per coin:**
   1. decode and validate the VTXO;
   2. check that the round was swept wholesale and is buried `sweep_min_confs` deep;
   3. skip the coin if it's in a pending round participation;
   4. ban it, then wait `ban_wait_secs`;
   5. claim it with a conditional `spend_state` update, which requires our ban to still be in place.
5. **Pay** in one batch: store the tx, then journal it (fsync), then broadcast.
6. **Check invariants.**

Code: [`src/main.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/src/main.rs) (tick and per-coin decisions), [`src/db.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/src/db.rs), [`src/chain.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/src/chain.rs), [`src/checks.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/src/checks.rs), [`src/journal.rs`](https://github.com/BullishNode/abandon-ship-server/blob/main/src/journal.rs).

**User side.** The fork's barkd adds three calls:
- **adopt** the server's coin status, so a paid coin leaves the Ark balance;
- **find** payouts at `tr(coin key)`;
- **sweep** them into the on-chain balance.

Code: [`bark/src/expiry_payout.rs`](https://github.com/BullishNode/abandon-ship-bark/blob/variant-b/bark/src/expiry_payout.rs). bark-web uses them in [`use-expired-vtxos.ts`](https://github.com/BullishNode/abandon-ship-client/blob/client-fixes/src/hooks/barkd/use-expired-vtxos.ts) and [`use-sweep-expiry-payouts.ts`](https://github.com/BullishNode/abandon-ship-client/blob/client-fixes/src/hooks/barkd/use-sweep-expiry-payouts.ts).

**Why a coin can't be paid twice:**
- **Refresh vs claim:** captaind's spends and our claim are both conditional updates on the same row, so exactly one wins.
- **Exit and payout:** payouts happen only after a wholesale sweep `sweep_min_confs` deep, so the user can't also exit.
- **Crash or restore:** the journal plus re-derivation at pay time.

Full guard table: [`docs/design.md`](design.md).

## Where everything is

All repos are public under BullishNode. bark-web and the forks are test benches. The BULL wallet will call the bark library from Rust.

| What | Repo | Branch | Role |
| --- | --- | --- | --- |
| Sidecar (Rust) | [abandon-ship-server](https://github.com/BullishNode/abandon-ship-server) | `main` | Pays expired coins; regtest stack in [`regtest/`](../regtest), scenarios in [`tests/regtest/`](../tests/regtest) |
| Test wallet UI | [abandon-ship-client](https://github.com/BullishNode/abandon-ship-client) | `client-fixes` | bark-web fork with Renewing / Paying out / Paid out states |
| Wallet library + barkd | [abandon-ship-bark](https://github.com/BullishNode/abandon-ship-bark) | `variant-b` | bark fork: adopt status, find payouts, sweep them |
| WASM bindings | [abandon-ship-bark-ffi](https://github.com/BullishNode/abandon-ship-bark-ffi) | `variant-b` | bark-ffi fork exposing the same calls; payouts need barkd mode for now |

- **Server stack:** captaind and watchmand from bark master `6768e0fb4` (docker `nightly-2026-10-01`), Bitcoin Core 31 and Postgres. The setup is in [`regtest/compose.yaml`](../regtest/compose.yaml).
- **Image `abandon-ship/bark:variant-b`:** barkd from the fork. Build it with [`contrib/docker/variant-b/Dockerfile`](https://github.com/BullishNode/abandon-ship-bark/blob/variant-b/contrib/docker/variant-b/Dockerfile).
- **Docs:**
  - [`README.md`](../README.md): flow and config;
  - [`docs/design.md`](design.md): scope, failures and guards, limitations;
  - [`docs/deployment.md`](deployment.md): setup and runbooks;
  - [`tests/edge-cases.md`](../tests/edge-cases.md): every case with its status;
  - [`docs/captaind-settle-plan.md`](captaind-settle-plan.md): the captaind patch.
- **Unfinished work** on branch `wip/cycle4-mutation` in [server](https://github.com/BullishNode/abandon-ship-server/tree/wip/cycle4-mutation) and [client](https://github.com/BullishNode/abandon-ship-client/tree/wip/cycle4-mutation): mutation-testing scenarios, untested.

## Walkthrough as the operator

On regtest a coin lives 64 blocks and the watchman sweeps every 30 s, so a full cycle takes minutes.

1. **Bring up the stack.** In `regtest/`, follow [`regtest/README.md`](../regtest/README.md):
   1. bitcoind and Postgres;
   2. the `faucet`, `payout` and `sweep` wallets;
   3. CLN, captaind and watchmand;
   4. fund the rounds and watchman wallets;
   5. create the `sidecar` schema ([`migrations/0001_sidecar.sql`](../migrations/0001_sidecar.sql));
   6. write `sidecar.toml` from [`config.example.toml`](../config.example.toml);
   7. run `./seed-fees`.
2. **Watch one coin get paid:** run [`tests/regtest/happy-single.sh`](../tests/regtest/happy-single.sh). It boards a coin, refreshes it into a round, mines past expiry, lets the watchman sweep and mines `sweep_min_confs`. It then runs sidecar ticks until the payout confirms. The log is in `/tmp/abandon-regtest/happy-single/sidecar.log`.
3. **Or drive it by hand:**
   - make a coin: `./bark <wallet> board …`, then `./bark <wallet> refresh --all`;
   - see captaind's view of it: `./coins`;
   - pass expiry: `./mineto <height>`;
   - run the sidecar: `cargo run --release -- regtest/sidecar.toml` (loop) or `--once` (one tick). Add `RUST_LOG=abandon_ship_server=debug` to see why each coin waits.
4. **Afterwards:**
   - `./coins` shows the coin as `spent`;
   - `./psql` then `SELECT * FROM sidecar.payout` shows the row moving from `claimed` to `signed`, `broadcast` and finally `confirmed`;
   - `regtest/payouts.journal` has the batch;
   - `./btc getrawtransaction <txid> 1` shows the output paying `tr(coin key)`.

The whole suite is [`tests/regtest/run-all.sh`](../tests/regtest/run-all.sh). Each scenario passes on its own, but the full suite hasn't yet completed in one clean run (see the open items below).

## Walkthrough as the user

1. **Start barkd from the fork:** in `regtest/`, run `docker compose --profile web up -d barkd`. The API is on `127.0.0.1:43000` with no auth (regtest only).
2. **Start bark-web.** In [abandon-ship-client](https://github.com/BullishNode/abandon-ship-client/tree/client-fixes), on branch `client-fixes`:
   1. `npm install`;
   2. copy [`.env.byob.example`](https://github.com/BullishNode/abandon-ship-client/blob/client-fixes/.env.byob.example) to `.env.byob` and set:
      - `BARKD_URL=http://localhost:43000`
      - `BARK_NETWORK=regtest`
      - `ARK_SERVER=http://captaind:3535`
   3. `npm run dev:byob`, then open `http://localhost:5173`.

   The env file only takes a cookie file for bitcoind. If that doesn't fit, create the wallet first through the barkd API, as [`web-journey.sh`](../tests/regtest/web-journey.sh) does: bitcoind at `http://bitcoind:18443`, user `second`, pass `ark`.
3. **Receive:** fund the on-chain address from the `faucet` wallet, board, and let the coin refresh into a round.
4. **Go offline:** stop using the wallet and mine past expiry. The coin shows as **Renewing**.
5. **Operator pays:** run sidecar ticks. When the wallet syncs, it adopts the server's `spent` status, finds the payout and shows **Paying out**. Once the payout confirms, it shows **Paid out**.
6. **Sweep:** press **Move to on-chain balance**. History shows one "Expired coin paid out on-chain" entry.
7. **Restore:** restoring the seed on a second barkd finds the same payout. So does a plain Bitcoin Core descriptor wallet with `tr(xprv/350'/0'/*)`.

[`web-journey.sh`](../tests/regtest/web-journey.sh) runs the same journey unattended against the barkd API. In the browser we drove it with Playwright. Payouts need barkd mode, because the published WASM package (`@secondts/bark` 0.24.0) doesn't have the new calls.

## What we tested

The user journey works end to end on regtest. The catalogue in [`tests/edge-cases.md`](../tests/edge-cases.md) has 172 cases:

| Status | Cases |
| --- | --- |
| pass | 68 |
| fixed (a real bug, found and fixed) | 24 |
| reasoned (n/a on this stack) | 15 |
| known gap | 10 |
| still to run | 55 |

| Area | Scenarios | Result |
| --- | --- | --- |
| Happy path | `happy-single`, `happy-batch`, `shared-address` | pass |
| User journey in bark-web | `web-journey` | pass |
| Unclaimed coins | `unclaimed-delegated` | pass |
| Races with the user | `race-held-lock`, `race-user-refresh`, `race-operator-unban`, `h2-probe`, `attack-ban-wait` | pass |
| Unilateral exits | `exit-full`, `exit-breaker`, `exit-blocked` | pass |
| Crash and restore | `crash-signed`, `db-restore`, `tamper-payout` | pass |
| Fees | `fee-no-estimate`, `fee-pct-rule`, `fee-stuck-claim`, `fee-window` | pass |
| Infrastructure | `infra-postgres`, `infra-bitcoind`, `schema-version`, `circuit-breaker` | pass |

Bugs fixed along the way:
- a fee-rate decimal that bitcoind rejected;
- small or unaffordable coins starving the payout queue;
- one stuck claim blocking all new claims;
- the journal missing the raw tx needed after a restore;
- one partial exit tripping the circuit breaker;
- the bark fork listing a payout that was already being swept, so the money showed twice.

## Decisions

- **One trust domain.** One operator runs captaind, its database, bitcoind and the sidecar, and all of them are trusted. Out of scope: database writers, compromised hosts, a malicious operator, deep reorgs.
- **Goal.** Every unpaid entitlement stays recoverable until it's settled, and none is settled twice. Eligible ones are settled when chain and fee conditions allow.
- **Fees.**
  - There's no fallback fee rate.
  - Each coin's fee share is capped at a percentage of the coin.
  - Coins below a minimum are never paid on-chain.
  - The fee comes out of the user's own output.
- **Returning users** refresh as normal during the grace period. Only coins nobody comes back for are paid.

## Open

- [ ] **One clean full run of `run-all.sh`.** A harness flake blocks it: captaind lags the chain tip after scenarios that mine many blocks.
- [ ] **The captaind patch** ([plan](captaind-settle-plan.md)): a `SettleVtxo` admin call that takes captaind's own coin lock. It would replace the ban-and-wait and remove the crash risk of writing captaind's table from outside. It's in progress on branch `captaind-settle`, in both the server repo and the bark fork, and will be pushed when its tests pass.
- [ ] **Known gaps:**
  - a payout rejected at broadcast stops the whole tick;
  - a fragmented payout wallet can defer a batch forever;
  - the bark fork may credit one payout to several coins with the same key;
  - bark-web never retries a coin the server once refused.
- [ ] **Partially exited rounds.** Pay them when a confirmed sweep spends an output on that coin's own exit path. Today they go to manual review.
- [ ] **Sweep straight into the payout wallet,** so there's no separate float.
- [ ] **Seed-only recovery at high, sparse key indexes,** using `scantxoutset`, with the tested range measured.
- [ ] **BULL wallet integration in Rust:** adopt, find and spend, using the fork's `expiry_payout` functions.
- [ ] **Rollout:** a single reviewer pass over `main`, then a signet pilot, then a capped mainnet pilot.
