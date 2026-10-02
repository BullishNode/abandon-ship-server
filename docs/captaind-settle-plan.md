# captaind patch: SettleVtxo admin RPC (plan, rev 2)

Base: bark master `6768e0fb4`. It's our own patch on the fork ([abandon-ship-bark](https://github.com/BullishNode/abandon-ship-bark), branch `captaind-settle` off `6768e0fb4`) and is not proposed upstream.

## Why a patch, and why this one
captaind's invariant: **a coin in its in-memory lock (`VtxosInFlux`, `server/src/flux.rs`) cannot change underneath it.** Every in-Ark spend takes that lock first:
- rounds: `round/mod.rs:591` and `:706`;
- arkoor: `arkoor.rs:99`;
- offboards: `offboards.rs:162`, held for the whole session;
- the vtxo pool: `vtxopool.rs:302`.

A round keeps it until the round ends (`locked_inputs`, `round/mod.rs:484`, `:540`). When a round's persist then finds an input changed, the error is **Fatal by design** (`persist_round` returns `RoundError::Fatal` at `round/mod.rs:1335`, and the coordinator returns it at `:1891`), so the process exits.

So the H2 crash is not a captaind bug. Our sidecar's direct `UPDATE vtxo` breaks captaind's invariant. Two consequences:
- **Fixing the NULL in the diagnostic query (`database/tree.rs:675`) would not help.** The error would still be Fatal. Only a write that takes the flux lock is correct.
- **The lock lives in captaind's memory,** so only code inside captaind can take it. That means an admin RPC; nothing outside the process can do this.

## Design
`SettleVtxo(vtxo_id)` on the admin interface. It has no auth by design: `rpcserver/admin.rs:1-5` describes the admin interface as an operator plane, the same as ban/unban at `:142`.

**Handler:**
1. `try_lock([vtxo_id])` on `self.vtxos_in_flux`. If the coin is held, return `IN_FLUX`.
2. One DB transaction:
   1. If a `round_participation` for a round that hasn't finished lists the coin as input (the query the sidecar's `in_round_participation` uses today), return `IN_ROUND_PARTICIPATION`. This makes a returning user's stored delegated refresh win.
   2. `UPDATE vtxo SET spend_state='spent', updated_at=now() WHERE vtxo_id=$1 AND policy_type='pubkey' AND confirmed_height IS NULL AND spend_state IN ('spendable','unclaimed')`.
   3. If it updated 1 row, `INSERT INTO settlement(vtxo_id, settled_at)` and return `SETTLED`.
   4. If 0 rows, return `ALREADY_SETTLED` if a `settlement` row exists, otherwise `NOT_SETTLEABLE`.
3. Drop the guard after the commit.

**Lock order:** flux first, then the DB transaction, the same as rounds, so there is no deadlock.

**Why the `settlement` table is needed** (rev 1 had it for the wrong reason):
- A claim is now an RPC, while the sidecar's payout row is in its own transaction. That leaves a crash window: settle succeeds, then the sidecar dies before writing its row.
- On restart, a repeat call must tell "we settled this" (`ALREADY_SETTLED`) apart from "the user spent it" (`NOT_SETTLEABLE`). Otherwise the entitlement is lost or ambiguous.
- The table only stores `vtxo_id` and `settled_at`. The payout txid stays in the sidecar's journal and row.

**Where the table lives: outside refinery.** If our patch adds a numbered migration, it collides with upstream's numbering at the next upgrade, and refinery can abort on divergent or missing migrations. So the patch runs `CREATE TABLE IF NOT EXISTS settlement (...)` at startup instead. captaind's migration history stays identical to upstream, and the sidecar's schema allowlist is unchanged.

**Sidecar claim flow:**
1. In the sidecar's own DB, insert a `payout` row in state `settling`.
2. Call `SettleVtxo`:
   - `SETTLED` or `ALREADY_SETTLED`: mark the row `claimed`;
   - `IN_FLUX` or `IN_ROUND_PARTICIPATION`: delete the `settling` row and retry next tick;
   - `NOT_SETTLEABLE`: delete the row (the user spent the coin in Ark).
3. On restart, every `settling` row is resolved by repeating step 2. Settle is idempotent, so this is safe.

## Races, checked against the code
| Race | Outcome |
| --- | --- |
| Settle vs interactive round, any phase | The flux lock is held by one side. The loser gets `IN_FLUX`, or the round gets a clean "input VTXO already locked" badarg. No Fatal, because the round can't take a coin that settle holds, and settle can't take one the round holds. |
| Settle vs a stored delegated participation | Settle refuses: the user wins. If the participation is inserted just after settle's check, the round later takes the lock, sees `spent` and drops that participation cleanly (`round/mod.rs:700`). |
| Settle vs arkoor or LN send | arkoor takes the lock and commits while holding it (`arkoor.rs:99-130`). Exactly one wins. LN send goes through arkoor into an HTLC coin; to confirm in tests. |
| Settle vs offboard | The offboard session holds the lock for its whole life, so settle gets `IN_FLUX` until the session ends. |
| Settle an `unclaimed` hArk output vs the owner's forfeit | Forfeit takes no lock, but its claim update is conditional on `'unclaimed'` and skips other rows silently (`database/tree.rs:801-812`, `round/forfeit.rs:199`). If settle commits first, the forfeit stores its forfeits and the output stays spent: the owner is paid on-chain once. If the forfeit commits first, the output is `spendable` and settle spends it: still paid once. No double payment either way. |
| Watchman | Sweep policy reads tree, arkoor and forfeit state (`watchman/policy.rs`), not `spend_state`. That matches what we already see: coins marked spent today are still swept normally. |
| Unilateral exit | Unchanged. The sidecar still pays only after a wholesale sweep `sweep_min_confs` deep. `confirmed_height IS NULL` in the update also refuses coins that have exited. |

## What the sidecar drops
- **Ban machinery:** the ban, the ban wait, the intact-ban condition, `banned_until_height` writes, the `sidecar.ban` table and the `ban_blocks` / `ban_wait_secs` config.
- **Writes:** the direct `UPDATE vtxo`, plus the `vtxo_history` insert it caused.
- **Tests:** scenario `race-operator-unban` (bans no longer exist) and the H2 ban timing in `h2-probe`. h2-probe stays: it becomes "settle at 0.5, 2 and 4.5 s into the submit window, and captaind never exits".

## What the sidecar keeps
- Direct **reads** of captaind's DB (candidates, coin blobs, participations) and the schema allowlist. A read that breaks after an upgrade fails closed: the sidecar waits. Writes were the dangerous coupling; reads aren't.
- The sweep check, journal, fee gate, invariants, leader lock and circuit breaker.

Estimated net: the sidecar loses ~150 lines; captaind gains ~120 plus tests.

## Patch contents
1. **Proto** (`server-rpc/protos/bark_server.proto:603`, `BanAdminService`, or a new `SettleAdminService`): `rpc SettleVtxo(SettleVtxoRequest{bytes vtxo_id}) returns (SettleVtxoResponse{SettleResult result})`.
2. **Handler** in `rpcserver/admin.rs`, plus DB functions in a new file `database/settle.rs`.
3. **Startup `CREATE TABLE IF NOT EXISTS settlement`.**
4. **CLI** `captaind rpc settle <vtxo_id>`, in `bin/captaind/main.rs` next to `ban`/`unban` at `:702`.
5. **Tests** in `testing/tests/server/settle.rs`, using the existing `ban.rs` setup:
   - settle a spendable coin, then refresh, arkoor and offboard are refused;
   - settle while an interactive round holds the coin returns `IN_FLUX`, and captaind keeps running;
   - settle with a stored delegated participation returns `IN_ROUND_PARTICIPATION`;
   - an unclaimed hArk output: settle, then forfeit (and the reverse order): exactly one outcome, nothing paid twice;
   - settle twice returns `ALREADY_SETTLED`; settle an exited or spent coin returns `NOT_SETTLEABLE`;
   - restart captaind with the table already present: it starts.

## Not in this patch (decide later)
- **Payout txid in `GetVtxoStatus`** (`rpcserver/ark.rs:158`). Useful to wallets, but needs a second call, `RecordSettlement(vtxo_id, txid)`, and a proto change in the client API. Wallets can already find payouts by scanning `tr(coin key)`.
- **Moving the sidecar's reads to RPCs.** It isn't needed for safety, and it would grow the patch without a safety gain.

## Order of work (one writer)
1. Write the patch and its captaind tests on `captaind-settle`. Build an image `abandon-ship/captaind:settle`.
2. Switch the sidecar to the RPC flow (`settling` rows). Delete the ban machinery.
3. Run the regtest stack with the patched image: `run-all.sh`, h2-probe at the three timings, web-journey.
4. Upgrade drill: rebase the patch onto a newer bark master, then rerun steps 1–3. This measures the real per-upgrade cost.

## Cost
- We build and run our own captaind image, and rebase about 4 files at every captaind upgrade.
- In exchange, the upgrade audit of every write to captaind's vtxo table is no longer needed, and the captaind crash is gone.

## Verification (2026-10-02, against `6768e0fb4`)
- **LN send goes through arkoor and the flux lock: yes.** `request_lightning_pay_htlc_cosign` (`ln/mod.rs:238`) and the LN receive claim (`ln/mod.rs:962`) both call `cosign_oor_with_builder`, which takes `vtxos_in_flux.try_lock` (`arkoor.rs:99`). The receive claim spends htlc-recv coins, which settle never touches (`policy_type='pubkey'` only).
- **arkoor commits while holding the guard: yes.** `arkoor.rs:99-123`: `try_lock`, then `db.write` (tree update and htlc resolutions), then `drop(vtxo_guard)`. Its spendability check (`cosign_oor`, `check_spendable_for_oor`) runs *before* the lock. That gap is safe: the write itself, `do_oor_spend_updates` (`database/tree.rs:620`), only updates rows that are `confirmed_height IS NULL` and `spendable`/`pool`/`htlc-recv-unclaimed`. Otherwise it returns a badarg ("unspendable"), a request error, not Fatal. Rounds and offboards are the same: lock (`round/mod.rs:591`, `:706`; `offboards.rs:162`), then `check_spendable`, then a conditional spend update (`do_round_spend_updates`, `do_offboard_spend_updates`). While the round holds the lock, settle can't change the coin, so `persist_round` can't hit the Fatal path.
- **round_participation query: the sidecar's (`forfeited_at IS NULL`) and captaind's own "pending" (`round_id IS NULL`, `database/query.rs:227`, `database/rounds/mod.rs:238`) agree on every coin settle can touch.** Lifecycle:
  - Interactive participations are written only by `finish_round`, in the same transaction that sets `round_id` and marks the inputs spent (`database/rounds/mod.rs:58-115`).
  - Delegated ones stay `round_id IS NULL` until a round consumes them (same atomic transaction) or rejects them (deleted, `round/mod.rs:805`).
  - `undo_round` deletes the participations (`database/rounds/mod.rs:423`).
  - So a participation with `round_id` set always has spent inputs, and settle refuses those anyway.
  - **Decision:** the patch uses `round_id IS NULL`, captaind's own definition.
  - Scheduled delegated refreshes must be scheduled before the input's expiry (`round/mod.rs:1930`). For an expired coin, every pending participation is therefore already due, and the next round consumes or deletes it. `IN_ROUND_PARTICIPATION` can't block settle forever.
- **Startup `CREATE TABLE IF NOT EXISTS` vs refinery: safe.** Refinery's runner (`database/mod.rs:148`) checks only `refinery_schema_history` versions: divergent or missing *migrations*. It never inspects other tables. `Db::create` checks emptiness (`database/mod.rs:205`) before any table exists. **Decision:** the table is created right after `Db::connect` in captaind's start path (`lib.rs:356`), not inside `Db::connect`. watchmand also calls `Db::connect` (`watchman/daemon.rs:122`), so this keeps the DDL to one process and avoids racing a concurrent startup.
  - **Residual risk:** an upstream migration that later creates a table named `settlement` would fail at upgrade. It fails loudly (captaind refuses to start), and the upgrade drill catches it.
- **Forfeit vs settle of an `unclaimed` output: as the plan says.** `round/forfeit.rs:172-203` takes no flux lock. `do_claim_updates` (`database/tree.rs:808`) only moves `unclaimed` to `spendable` and silently skips other rows.
- **Service placement:** `SettleVtxo` is added to the existing `BanAdminService`. No new service registration or re-export, so it touches fewer files at each rebase.
- **Sidecar DB-restore path:** `reassert_paid` (journal says paid, coin spendable again after a DB restore) was also a direct `UPDATE vtxo`. It now calls `SettleVtxo` too. After a restore the `settlement` row is gone as well, so settle returns `SETTLED` and the coin is spent again under the flux lock. `IN_FLUX`/`IN_ROUND_PARTICIPATION` retries next tick; the coin stays quarantined either way.
  - **Open (not handled, by "no extra features"):** after a DB restore, if the owner has stored a delegated refresh of an already-paid coin, settle returns `IN_ROUND_PARTICIPATION`. The next round could then refresh a coin that was paid on-chain. This needs both a DB restore and a returning owner. The old direct `UPDATE` had the same window against a running round, and it crashed captaind besides. A fix would be a restore-only mode that deletes the pending participation; that's for the owner to decide.
