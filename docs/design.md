# Design

## Scope

- **Trusted:** captaind, its Postgres DB, bitcoind, the sidecar and its host. One operator runs all of them; only captaind and the sidecar write captaind's DB.
- **Out of scope:** DB writers, compromised hosts, a malicious operator, deep reorgs.
- **Goal:** every outstanding entitlement remains represented in durable, recoverable state until settlement. No entitlement is settled twice. Eligible entitlements are eventually settled when the required chain, fee, funding and service conditions hold.

## Failures and guards

| # | Failure | Guard | Scenarios (`tests/regtest/`) |
| --- | --- | --- | --- |
| 1 | The same coin is refreshed, exited **and** paid | Conditional claim racing captaind's conditional spends (exactly one wins). Ban, then wait longer than a round; the claim requires our ban intact. No claim while the coin is in a round participation. Payout only after a wholesale sweep `sweep_min_confs` deep; leaf-confirmed coins are never candidates | `race-user-refresh`, `race-held-lock`, `race-operator-unban`, `claim-gates`, `h2-probe`, `exit-full`, `exit-blocked`, `unclaimed-delegated` |
| 2 | Paying the wrong branch of a partially exited round | A funding output spent by a tree tx quarantines the round's remaining coins (planned: pay when a confirmed sweep spends an outpoint on the coin's own exit path) | `exit-breaker` |
| 3 | A crash or a DB restore loses an outstanding payment | Tx stored in the DB, then appended with its raw tx to the local journal (fsync), then broadcast; stored txs are rebroadcast, never rebuilt. A rejected stored payout keeps its inputs reserved while other batches proceed. A journaled coin spendable again after a restore is re-marked spent and its journaled tx rebroadcast. Ledger rows only move forward | `crash-signed`, `db-restore`, `tamper-payout`, `broadcast-rejected` |
| 4 | Fees or selection block eligible payouts indefinitely | No estimate: no claims, no payouts (no fallback rate). Per-coin rule: pay only if the fee share is ≤ `max_fee_pct_per_payout` and the coin ≥ `min_payout_sat`; smaller coins and coins unaffordable at the current rate are filtered before the candidate limit. A coin that cannot be processed waits or is quarantined alone; a burst of quarantines stops the process (one partially unrolled round counts once). Safe unlocked payout inputs are selected largest first. Existing claims get the first payment attempt; a batch failing its actual fee or funding check is reduced, and deferred claims do not block new claims | `fee-pct-rule`, `fee-no-estimate`, `circuit-breaker`, `happy-batch`, `fee-stuck-claim`, `fee-window`, `fee-fragmented`, `fee-fragmented-progress`, `exit-breaker` |
| 5 | A restored wallet cannot find or spend its payouts | Payout to BIP86 `tr(coin key)`, derivable from the seed; the bark fork adopts the spent state, finds the payout and sweeps it | `web-journey`, `unclaimed-delegated` |

Process: one instance (Postgres advisory lock); the captaind schema version is allowlisted and checked every tick; the payout tx is verified (outputs, amounts, fee share, recipient deductions equal the full mining fee, change ours) before it is stored.

## Limitations

- **Quarantine** (a coin is never touched again automatically; manual review):
  - `undecodable vtxo`: released by a sidecar build that reads the new encoding; the operator then deletes the row from `sidecar.quarantine` and the coin is a candidate again.
  - `unparseable spender txid`: as above, for a change in how captaind records the sweep.
  - `round partially unrolled`: permanent; the owner can still redeem the coin through captaind.
  - `already paid per local journal`: permanent; the coin was paid, the DB was restored.
- **Waiting on fees** keeps the coin with the operator: no estimate or a fee share above the cap leaves it unpaid, refreshable by its owner, until fees fall.
- **Confirmation floor:** with `grace_blocks` ≥ 144 above `sweep_min_confs` ≥ 100 (mainnet floors), a sweep made at expiry is already deep enough when the grace period ends; `sweep_min_confs` only matters for late sweeps.
- **Reorgs:** a payout is not re-checked after 6 confirmations; a deeper reorg that drops it goes unnoticed.
- **No RBF bump:** a payout stuck at a low fee is rebroadcast but not bumped.
- **Client side:** captaind does not tell wallets that a coin was paid out. A wallet must ask (`GetVtxoStatus`), find the payout at `tr(coin key)`, and sweep it. Until it does, the coin still appears in its balance and refreshes of it are refused.
- **Dependence on captaind's DB:** column names, `spend_state` values and the stored VTXO encoding are captaind internals; every captaind upgrade goes through the gate in `deployment.md`.
