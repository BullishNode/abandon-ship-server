# Threat model: abandon-ship-server (draft, 2026-10-01)

Scope: the sidecar as scaffolded, running next to a Bull-run captaind. The goal is to fix anything that would force a refactor later, before testing starts.

## What can be stolen or broken

| Asset | Loss if compromised |
| --- | --- |
| Hot payout wallet (bitcoind wallet `payout`) | Its float |
| captaind's database (the sidecar writes to it) | Wrong claims, i.e. users' coins wrongly closed; or anything else the DB role can touch |
| Correctness of payouts | Paying the wrong amount or destination, or paying twice |
| Liveness | Payouts stall, or captaind rounds halt |

## Attackers and what each can do against the scaffold

| # | Attacker | Attack | Scaffold today | Fix (now, to avoid a refactor) |
| --- | --- | --- | --- | --- |
| T1 | Anyone with DB write: SQL access, a captaind bug, or a compromised DB host | Insert or edit a `vtxo` row (amount, policy, anchor) so the sidecar pays them | **Trusts DB columns** for amount, key and anchor | **Trust the chain, not the DB.** Decode the full `vtxo` blob and run `Vtxo::validate(&anchor_tx)`. This checks the whole signed chain against the on-chain funding tx. Take the amount and pubkey from the validated VTXO. Per swept round, Σ payouts ≤ funding output value (I1), enforced before paying. |
| T2 | Same as T1, or a gap in captaind's rows | Make a **tree tx** look like a sweep. The coin is then still exitable, and gets paid **and** exited. | Detects a sweep through `vtxo_txid` rows in the DB | **Detect the sweep on-chain.** The tx spending the funding outpoint must pay **only** to Bull's sweep script(s), taken from config. Any other spender is a tree tx and goes to manual review. |
| T3 | A malicious user | A coin with odd data (undecodable policy, missing anchor tx) makes a tick fail. With `?` everywhere, **one bad coin halts all payouts.** | Halts | **Per-coin fault isolation.** A per-coin error logs the coin and moves it to `sidecar.quarantine`, and the loop continues. Only infrastructure errors (DB down, bitcoind down) stop the tick. |
| T4 | Anyone who reaches captaind's admin gRPC | The admin API also exposes wallet, sweep and unban calls. A sidecar on another host needs that port reachable. | Calls `BanVtxo` over gRPC | **Drop the admin API.** captaind's `BanVtxo` is just `UPDATE vtxo SET banned_until_height` (`server/src/database/ban.rs:14-27`). The sidecar does the same update itself, keeping tip + blocks < 2³¹. That removes the tonic/prost dependencies, and the admin port stays on localhost. |
| T5 | A compromised sidecar host | Use its DB credentials to tamper with other captaind state, such as forfeits and rounds | Not defined | **Least-privilege DB role:** `SELECT` on the tables it reads, `UPDATE (spend_state, banned_until_height, updated_at)` on `vtxo` only, and ownership of schema `sidecar`. Nothing else. |
| T6 | A compromised sidecar host | Drain the payout wallet, or call other bitcoind RPCs | Full RPC user | **Separate bitcoind wallet with a small float**, topped up from the sweep wallet. An RPC user restricted with `rpcwhitelist` to only the calls the sidecar uses. The sweep and rounds wallets are not reachable with that user. |
| T7 | A fee estimator that is poisoned (fee spam) or broken | Payouts burn users' value in fees | No cap | **Fee cap.** Before storing, check feerate ≤ `max_fee_rate` and total fee ≤ `max_fee_share` of outputs; otherwise defer. |
| T8 | A bitcoind or wallet bug, or a compromise | The built tx pays something other than intended | Not checked | **Verify the tx before storing it.** Every expected output is present, with amount = expected − fee share; the only other output is change to the payout wallet; the inputs are payout-wallet UTXOs. |
| T9 | An operator mistake: two instances, or a restart mid-batch | Duplicate batches | `mark_signed` guard only | **Single leader** via `pg_try_advisory_lock` at startup. Exit if it is not held. |
| T10 | A captaind upgrade | Schema or semantics change silently; the conditional updates may no longer hold | Not checked | **Startup guard.** Read captaind's migration version, and refuse to run unless it is in an allowlist the upgrade gate has signed off. |
| T11 | Supply chain | A malicious crate | `ark-lib` pinned by git rev | Commit `Cargo.lock`; run `cargo deny` / `cargo audit` in CI; keep dependencies minimal (T4 removes three). |
| T12 | Public repo | A secret committed by accident | `config.toml` ignored | Keep it so. Regtest uses test-only passwords. Production config lives outside the repo. |

## Out of scope for the sidecar (covered elsewhere)
- captaind's own bugs: report 05; the upgrade gate.
- A user redeeming and getting paid at the same time: closed by the atomic claim (design §5). The race tests prove it.
- An exit and a payout for the same coin: closed by the 100-confirmation sweep requirement, plus T2's on-chain check.
- Reorgs deeper than 100 blocks: accepted.

## Status

T1–T4 and T7–T10 are implemented in code. T5 and T6 are in `docs/deployment.md`. Each still needs proving on regtest.

## Changes made before testing
1. Validate VTXOs against the chain and enforce I1 (T1).
2. Detect sweeps on-chain from the configured sweep scripts (T2).
3. Quarantine bad coins per coin (T3).
4. Write the ban to the DB and drop the gRPC client (T4).
5. Fee cap and pre-store tx verification (T7, T8).
6. Advisory-lock leader and captaind schema-version guard (T9, T10).
7. A deployment doc for the DB role and bitcoind `rpcwhitelist` (T5, T6).
