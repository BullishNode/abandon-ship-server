# Architecture review: abandon-ship-server (independent observer, 2026-10-01)

**Scope.** I reviewed a read-only snapshot of the repo taken at 19:42 local time. Its `src/` sha256 prefixes are: main.rs `904716e25e2c`, db.rs `f2ce99150c7f`, chain.rs `944b76c2bbd9`, journal.rs `d05f9740b6e7`, config.rs `e2c3ec25333e`. Source was changing while I worked: `journal.rs` appeared mid-review, so line numbers refer to that snapshot.

**What I compared against.**
- captaind source at `8c29e300c` (`~/bark-integration-2026-10-01/src/bark`). `git diff 8c29e300c 6768e0fb4 -- lib server` is empty, so the pinned rev has the same code. **VERIFIED.**
- The regtest captaind logs and a read-only look at the regtest database.
- `cargo test` (4 pass) and `cargo clippy` (2 cosmetic warnings), both run in `/tmp/claude-1000/observer-copy`. `cargo audit --no-fetch` reported no advisories.

**Tags.** VERIFIED means I read the code, a log or the database. INFERRED means reasoned from verified facts but not run. UNVERIFIED means not checked.

---

## A. Architecture-level concerns

### A1. Coins refreshed by delegation are never paid. This is new and is the one finding that could force a design change.

**Severity: High.**

**Evidence.**
- Prevention step 2 registers a scheduled delegated refresh for every coin (`ark-expired-coin-handling.md` §1).
- When such a refresh runs, the input becomes `spent` with `spent_in_round` set, and the new output is inserted as `unclaimed` (`server/src/database/rounds/mod.rs:114-115`). **VERIFIED.**
- The output moves to `spendable` only when the user returns and forfeits the input (`round/forfeit.rs:196-202`). **VERIFIED.**
- The sidecar selects only `spend_state='spendable'` (`src/db.rs:67`). **VERIFIED.**

**Consequence.** A user who followed the prevention path and then stays away longer than about two lifetimes ends with value in an `unclaimed` output, and the sidecar never pays it. That is the exact population the sidecar exists for. Report 06 §4.5 identified this case, but the final design dropped it, and it is not among the 120 edge cases. **INFERRED.**

**Simplest fix (needs regtest proof).**
1. Also select pubkey round outputs where `spend_state='unclaimed'`, and allow the same state in the claim condition.
2. No ban is needed for these coins. The only competing writer is the forfeit path, which runs outside the round coordinator, so a collision cannot halt rounds (no H2).
3. If the forfeit commits first, the output becomes `spendable` and goes through the normal ban-and-claim path.
4. If the claim commits first, the forfeit still succeeds. `do_claim_updates` silently skips rows that are not `unclaimed` (`database/tree.rs:801-812`), so the preimage is still released (`forfeit.rs:207`).
   - That release should be worthless, because the output's round is swept at least 100 blocks deep and captaind refuses to spend a `spent` row. **INFERRED; prove it on regtest.**
5. Never pay the old input: it is `spent`, so the current filter already excludes it.

If this is not done, the design should say explicitly that these users are handled by hand, and the operator needs a metric for them.

### A2. O6: one exit removes the whole round from automatic payouts

**Severity: Medium. No redesign needed.**

- The sidecar quarantines every coin whose funding output was spent by a tree tx (`src/main.rs:169-175`). **VERIFIED.**
- A simple generalisation keeps the "single outpoint" style:
  - walk the coin's own genesis transactions from the anchor down;
  - each output on that path has only two possible spenders: the coin's next path tx, or the server's sweep after expiry;
  - the coin is eligible as soon as the first output on its path that is not spent by its own next tx is spent by a sweep at least `sweep_min_confs` deep;
  - if the coin's own leaf tx confirmed, quarantine it, because the user is exiting.
- That is about 25 lines, and it keeps the same security argument. **INFERRED.**
- It relies on `onchain_spent_txid` hints for internal node rows. O2 verified that hint only for funding outputs, so internal rows are **UNVERIFIED**.

**Recommendation:** keep the manual queue for v1, add a metric for it, and build the walk only if the queue is non-trivial.

### A3. O12 / #109 / #110: a DB restore can cause a double payment

**Severity: High before the journal, Medium with it.**

The snapshot adds `src/journal.rs`, an append-only file fsynced before broadcast. It is checked for candidates (`main.rs:94-100`) and for claimed rows (`main.rs:213-215`). Remaining gaps:

1. **The journal is written after `mark_signed`** (`main.rs:260-262`).
   - A crash between the two leaves a stored tx that `settle_inflight` rebroadcasts (`main.rs:272-277`) without ever journalling it.
   - A later restore then pays that coin again. **VERIFIED order; consequence INFERRED.**
   - Fix: in `settle_inflight`, journal every `signed` or `broadcast` row that is missing from the journal before (re)broadcasting it. That is one call.
2. **Reassertion is lazy and bounded.**
   - Only coins that land in the current `LIMIT max_batch` window are re-marked spent (`main.rs:93`, `db.rs:73`).
   - A restored coin is honoured by captaind (refresh, arkoor) until the sidecar reaches it, and Bull loses that money. **INFERRED.**
   - Fix: at startup, run one `UPDATE … WHERE vtxo_id = ANY($journal) AND spend_state='spendable'`.
   - Add a runbook rule: after any captaind DB restore, start the sidecar before captaind.
3. **Restores are dangerous for captaind itself.** A restore resurrects coins that were spent through arkoor after the backup, whatever the sidecar does (report 05 test 17, `05-protocol-bug-risk.md:240`). The real fix is operational: no point-in-time restore of captaind's DB without the runbook. Treat the journal file like the database: never roll it back.

### A4. O20 / H2: interactive participations are invisible before persist

**Severity: Low residual risk, liveness only. No redesign needed.**

**Facts. VERIFIED.**
- captaind stores interactive participations only inside `finish_round` (`rounds/mod.rs:56-100`).
- A persist failure becomes `RoundError::Fatal` and the process exits (`round/mod.rs:1289-1335`, `:1891-1894`). Log: `2026-10-02T01:26:27Z "Fatal round error … failed to find bad vtxo … unexpected number of rows"`, followed by "critical worker stopped".
- Persist (`round/mod.rs:1141`) comes before the funding broadcast (`:1177`), so no funds are at risk.
- The odd error text is a captaind bug. In the diagnostic query, `v.spent_in_round = u.round_id` with a NULL `spent_in_round` makes the row disappear, so `query_one` sees 0 rows (`database/tree.rs:671-684`). Worth reporting upstream along with "persist failure should not be fatal".

**The ban works as a guard. VERIFIED.**
- Every interactive submission re-runs `check_spendable`, including the ban (`round/mod.rs:591-596` → `:443`; `database/model.rs:234-238`).
- The claim requires the sidecar's own ban to still be in place (`db.rs:170-171`).
- So the remaining window is only a submission accepted before the ban commit and persisted more than `ban_wait_secs` later.

**Fixes.**
- Size `ban_wait_secs` against production `round_submit_time + round_sign_time` plus slack.
- Make `ban_blocks` small (3 to 6). Today it is 1000 in `config.example.toml`, about 7 days. A sidecar that stalls after banning locks the user out of refresh for that whole time. Too short costs nothing: the coin just gets re-banned. **INFERRED.**

### A5. Dependence on captaind's DB schema and semantics

**Severity: Medium. Accepted by design, but the guard has a hole.**

**What the sidecar depends on. VERIFIED by reading the queries.**
- `vtxo` columns: `vtxo_id`, `vtxo` blob, `policy_type`, `spend_state` (enum), `confirmed_height`, `expiry`, `amount`, `banned_until_height`, `onchain_spent_txid`, `vtxo_txid`, `spent_in_round`, `oor_spent_txid`, `offboarded_in`, `updated_at`.
- `round_participation(id, forfeited_at)`, `round_part_input`, `refinery_schema_history`.
- The `vtxo_update_trigger` → `vtxo_history` side effect.
- Semantics: every spend path is a conditional update that commits before release.
  - Arkoor: `tree.rs:613-650`, then signs after the commit (`arkoor.rs:124-133`).
  - Round: `tree.rs:659-693`.
  - Offboard: `tree.rs:702-747`; `register_offboard` runs before the broadcast (`offboards.rs:495-509`).
  - Lightning `ln-spent`: `ln/mod.rs:851-855`.
- The only unconditional state reset is `undo_round` (`tree.rs:785-797`). It is admin CLI only (`bin/captaind/main.rs:655`, behind `--dangerous`) and touches only `spent_in_round` rows.

**Hole.** T10 is checked only at startup (`main.rs:62-64`). If captaind is upgraded under a running sidecar, the gate is bypassed until the sidecar restarts. **VERIFIED.**
- Fix: re-check the version every tick. It is one query.

**Second hole.** An `ark-lib` encoding change makes every candidate fail to decode, and each one is quarantined permanently (`main.rs:123-125`).
- Fix: a circuit breaker. If more than N coins are quarantined in one tick, stop the process instead.

### A6. Fragility of the typed bitcoind RPC client

**Severity: Low. Not architectural.**

- The calls that broke (`getrawtransaction` verbose, `getwalletinfo`) are already untyped (`chain.rs:39`, `:53-58`).
- Typed calls remain: `get_block_count`, `wallet_create_funded_psbt`, `wallet_process_psbt`, `finalize_psbt` and `send_raw_transaction` (`chain.rs:43`, `:91-93`, `:103`). **VERIFIED.**
- Their result shapes are stable. The risk is in how the options are encoded across Core versions.
- Fix: move these five to `c.call::<serde_json::Value>` too and drop `bitcoincore-rpc`. That is about 30 lines and also removes the duplicate `base64 0.13`.
- The jsonrpc HTTP timeout defaults to 15 s (`jsonrpc-0.18.0/src/http/simple_http.rs:27`). That is acceptable. **VERIFIED.**

### A7. The sidecar claims coins it cannot pay yet. This is new.

**Severity: Medium.**

- Claims continue while payouts are deferred: fee cap hit (`main.rs:246-253`), wallet empty, or a stuck `signed` tx.
- Each claim takes a user's coin out of Ark with no payout in sight.
- `payouts_in_state('claimed')` has no LIMIT (`db.rs:201-204`), so the batch grows without bound. It can pass standardness limits or the `as u16` cast (`chain.rs:82`), and then stalls permanently. **VERIFIED code; consequence INFERRED.**
- Fix:
  1. Skip the claim loop while any `claimed` rows exist.
  2. Fetch at most `max_batch` claimed rows.

---

## B. Code-level issues (`src/`)

| # | Issue | Ref | Tag | Simplest fix |
|---|---|---|---|---|
| B1 | A non-hex `onchain_spent_txid` from the DB aborts the whole tick, including payouts, on every tick. This violates T3 (#84). | `main.rs:159` (`Txid::from_str(&spender)?`) | VERIFIED | Quarantine the coin instead. |
| B2 | Operator bans are overwritten. A coin banned by someone else gets `ban_age_secs = None` (`db.rs:107-114`), so the sidecar re-bans it with its own height and later claims it. A manual hold is lost. | `main.rs:185-189`, `db.rs:119-131` | INFERRED | Skip coins whose `banned_until_height > tip` and that have no matching `sidecar.ban` row. |
| B3 | The validated VTXO's **policy is not checked**. `user_pubkey()` also returns a key for HTLC policies, so a DB writer can relabel `policy_type`. Also, `validate` trusts the VTXO's self-declared `server_pubkey`. | `main.rs:197`; `lib/src/vtxo/policy/mod.rs:1066-1073`; `lib/src/vtxo/validation.rs:121-140` | VERIFIED | Require `VtxoPolicy::Pubkey`, and require `server_pubkey == cfg.server_pubkey`. A forged anchor is already net-zero because of T2, but this check is cheap. |
| B4 | Journal ordering and the rebroadcast path, as in A3. | `main.rs:260-262`, `:272-293` | VERIFIED | Journal inside `settle_inflight` before broadcasting. |
| B5 | Candidates can starve. `ORDER BY expiry LIMIT max_batch` keeps returning the same coins while they `Wait` forever: an unswept round, O9 config waits, #119 sweeps never recorded. Newer coins are never examined. | `db.rs:62-76` | INFERRED | Rotate with an expiry cursor, or skip coins checked in the last N blocks. |
| B6 | The claimed batch is unbounded, as in A7. | `db.rs:201-204` | VERIFIED | `LIMIT max_batch`. |
| B7 | `settle_inflight` rebroadcasts `signed` rows with `?`. One permanently invalid stored tx (#55) blocks every tick forever. This fails closed, which is correct, but nothing alerts. | `main.rs:275` | VERIFIED | Keep it failing closed, and alert. |
| B8 | I2 uses an inner `JOIN`, so a payout whose `vtxo` row was deleted (`undo_round` deletes round outputs, `rounds/mod.rs:444-448`) is skipped silently. | `db.rs:247-256` | VERIFIED | Use `LEFT JOIN` and flag `v.vtxo_id IS NULL`. |
| B9 | Postgres runs with `NoTls`, but `deployment.md` says "own host or container". Off-host, the password and DB traffic travel in cleartext. bitcoind RPC has no TLS either. | `db.rs:36`; `deployment.md` Network | VERIFIED | Simplest: run on the DB host over a unix socket or localhost. Otherwise use a tunnel. |
| B10 | Privileges, part 1: the role **owns** the ledger tables. On regtest it holds DELETE/TRUNCATE on `sidecar.payout`, so it can erase the ledger (#110). | `deployment.md:11`; read-only regtest grant query | VERIFIED | The DB admin owns the tables; grant `SELECT, INSERT, UPDATE` only. Then remove `migrate()` at startup, because `CREATE TABLE IF NOT EXISTS` needs CREATE (same trap as O11). |
| B11 | Privileges, part 2: `GRANT SELECT ON round_participation` exposes the plaintext `unlock_preimage` (05 §hArk (b)) to a compromised sidecar host. | `deployment.md:13` | VERIFIED | Column grant: `SELECT (id, forfeited_at, round_id)`. |
| B12 | The doc contradicts the code. T8 says "the inputs are payout-wallet UTXOs", but `verify_payout` never looks at inputs, and the fee is bitcoind's own report. | `checks.rs:35-70`; `threat-model.md` T8 | VERIFIED | Correct the doc, or check prevouts with `getaddressinfo`. |
| B13 | `MAX(version)` returns NULL on an empty table, and `get::<i32>` then panics. Startup only. | `db.rs:52` | VERIFIED | Use `Option<i32>`. |
| B14 | The comment says `is_unspent` returns true for "unknown"; `gettxout` returns null, so it returns false. The code is harmless; the comment is wrong. | `chain.rs:62-67` | VERIFIED | Fix the comment. |
| B15 | `Outcome::Lost` logs "user redeemed first" also when our ban lapsed. | `db.rs:164-176`, `main.rs:107` | VERIFIED | Log text only. |
| B16 | Dust is quarantined permanently, so the review queue fills with expected cases. At the 50 sat/vB cap the floor is about 7.8k sat. | `main.rs:146-147` | VERIFIED | Give these a separate reason or state that is not counted as needing review. |
| B17 | Stale docs: README says "Scaffold, not tested against a live captaind yet"; `edge-cases.md` says "50" but lists 120; README says "Verified against 8c29e300c" while the pin is 6768e0fb4 (same code, see Scope). README's "stale participations" item is resolved: due ones are dropped at the next round (`round/mod.rs:802-813`), and future-scheduled ones block only until their height. | `README.md`, `tests/edge-cases.md` | VERIFIED | Update the docs. |

Checked and fine:
- **The claim is atomic under READ COMMITTED.** The conditional UPDATE re-checks its condition after taking the row lock (#52 passed). **VERIFIED.**
- **All SQL is parameterised.** **VERIFIED.**
- **The I1 sum is serialised per anchor** (`db.rs:163`). Its `hashtext` int4 keys cannot collide with the bigint leader key. **VERIFIED.**
- **Pay-time revalidation** (`main.rs:219-229`) is good, but a transient anchor fetch error aborts the tick, which is acceptable.
- **The `coin_key_descriptor` example matches Bark's derivation:** 350' hardened / 0' hardened / i unhardened (`bark/src/lib.rs:480-483`, `derive_vtxo_keypair`). **VERIFIED.**

---

## C. Process observations

**Testing gaps.**
- About 56 of roughly 104 tracked edge cases are still `todo`.
- Unit tests cover only `checks`, `payout` and `journal`.
- There is no scripted regtest suite (`tests/README.md` is a to-do), and the race-×100 runs are not automated.
- Not covered:
  - delegated refreshes whose output is never claimed (A1);
  - a crash between `mark_signed` and the journal write;
  - DB restore followed by a user refresh before reassertion;
  - claiming while payouts are deferred (A7);
  - the operator-ban overwrite (B2);
  - the `rpcwhitelist` user.

**Where regtest differs from production.**

| Item | Regtest | Production |
|---|---|---|
| `sweep_min_confs` | 6 | 100 |
| `grace_blocks` | 10 | 1008 |
| `ban_wait_secs` | 40 s | 120 s |
| `min_payout_sat` | 630 | 7830 |
| Fee cap | 2 sat/vB | 50 sat/vB |
| `vtxo_lifetime` | 300 | — |
| `round_interval` | 30 s | — |

Other differences:
- **bitcoind RPC user:** regtest uses `second`, a full-rights user, so `rpcwhitelist` (T6) has never been exercised. `getwalletinfo`, for example, is outside a typical whitelist test.
- **Fees:** `-fallbackfee` is set, and the logs show "fee estimation failed, using fallback" every minute, so `conf_target` and the T7 cap never see real estimates.
- **Wallets:** one bitcoind also holds a `faucet` wallet, which contradicts "payout is the only wallet".
- **Network:** everything is on one Docker network, so the NoTls gap does not show up.
- **Journal path:** it is relative (`regtest/payouts.journal`).
- **What matches:** the DB role is least-privilege on regtest (grants **VERIFIED**), apart from the B10 ownership issue.

**What the upgrade gate must include.** It currently has the `spend_state` grep, the race suite and draining participations. Add:
1. Pin the sidecar's `ark-lib` rev to captaind's exact rev, and run a decode-and-validate pass over all spendable rows before bumping `allowed_schema_versions`.
2. Re-verify that `check_spendable` still honours `banned_until_height` (`model.rs:234`), and that interactive submissions still call it.
3. Re-verify that `finish_round` still inserts outputs as `unclaimed` and that forfeits still flip them (A1).
4. Re-verify that honour-on-return still works: refresh of expired coins after the sweep (O1).
5. Diff the `vtxo_update_trigger` columns and the role grants.
6. Re-run H2 and record whether a persist failure is still fatal.
7. Check that the Core version still decodes P2A (O10) and that `walletcreatefundedpsbt` options are unchanged.
8. Make the sidecar re-check the schema version every tick (A5).

---

## D. Top 10 actions, ranked

1. **Handle delegated-refresh outputs that are never claimed (A1).** Either extend the candidate and claim conditions to `unclaimed` round outputs and prove it on regtest, or document them as a manual case and add a metric.
2. **Stop claiming coins that cannot be paid (A7, B6).** Skip claims while `claimed` rows exist, and LIMIT the batch to `max_batch`.
3. **Close the journal gaps (A3, B4).** Journal inside `settle_inflight`, re-mark every journalled coin spent at startup, and write the runbook: no captaind DB restore without starting the sidecar first.
4. **Tighten privileges (B10, B11).** The DB admin owns the `sidecar` tables (no DELETE/TRUNCATE for the role), `round_participation` gets a column-level SELECT, `migrate()` is removed from startup, and the `rpcwhitelist` user is tested on regtest.
5. **Re-check the schema version every tick, and add a quarantine circuit breaker (A5).**
6. **Validate the coin's policy and server key, not just its signatures (B3).**
7. **Respect bans set by others, and shorten `ban_blocks` to about 3 to 6 (B2, A4).**
8. **Fix per-coin isolation and starvation.** Quarantine on a bad spender txid, and rotate the candidate window (B1, B5).
9. **Automate the regtest suite.** Include the race and crash cases listed in C, a production-like `ban_wait` and round timings, and alerts on quarantines and stuck `signed`/`broadcast` rows (B7).
10. **Small hardening.** Drop `bitcoincore-rpc` for untyped calls (A6), co-locate with Postgres or use TLS (B9), and file the upstream issues with Second: the NULL in the round-spend diagnostic query, fatal persist, `SettleVtxo`, and releasing the preimage on expiry.
