# Design

## Scope

- **Trusted:** captaind, its Postgres DB, bitcoind, the sidecar and its host. One operator runs all of them; only captaind and the sidecar write captaind's DB.
- **Out of scope:** DB writers, compromised hosts, a malicious operator, deep reorgs.
- **Goal:** every outstanding entitlement remains represented in durable, recoverable state until settlement. No entitlement is settled twice. Eligible entitlements are eventually settled when the required chain, fee, funding and service conditions hold.

## Failures and guards

| # | Failure | Guard | Scenarios (`tests/regtest/`) |
| --- | --- | --- | --- |
| 1 | The same coin is refreshed, exited **and** paid | Conditional claim racing captaind's conditional spends (exactly one wins). Ban, then wait longer than a round; the claim requires our ban intact. No claim while the coin is in a round participation. Payout only after a sweep of an exact outpoint on the validated coin exit path, `sweep_min_confs` deep; leaf-confirmed coins are never candidates. Unclaimed hArk replacements also require the same exact-path sweep proof for every original participation input | `race-user-refresh`, `race-held-lock`, `race-operator-unban`, `claim-gates`, `h2-probe`, `exit-full`, `exit-blocked`, `unclaimed-delegated`, `unclaimed-input-exit` |
| 2 | Paying the wrong branch of a partially exited round | A confirmed sweep must spend an exact outpoint on this coin's validated exit path. A swept sibling, including a different output of the same transaction, never qualifies | `exit-full`, `exit-breaker`, `exit-blocked` |
| 3 | A crash or a DB restore loses an outstanding payment | Tx stored in the DB, then journaled as one complete batch with raw bytes (fsync), then broadcast. Partial final records are replaced before appending. Restored claims reattach the journaled transaction as a batch; a confirmed peer cannot hide a pending restored member; the journal retries it even when the payout rows are missing. Captaind's startup wrapper waits for a fresh completed reassertion before serving clients. Reassertion runs before Core access; missing or conflicting Ark history prevents the completion marker. A rejected stored payout keeps its inputs reserved while other batches proceed. A journaled coin spendable again after a restore is re-marked spent and its journaled tx rebroadcast. Ledger rows only move forward | `crash-signed`, `db-restore`, `restore-retry`, `restore-postgres`, `restore-missing-history`, `restore-startup`, `restore-kill`, `restore-mixed-batch`, `restore-gate`, `tamper-payout`, `broadcast-rejected` |
| 4 | Fees or selection block eligible payouts indefinitely | No estimate: no new claims or transactions; stored payouts still retry (no fallback rate). Per-coin rule: pay only if the fee share is ≤ `max_fee_pct_per_payout` and the coin ≥ `min_payout_sat`; smaller coins and coins unaffordable at the current rate are filtered before the candidate limit. Stable expiry/ID pagination moves past waiting rows; new bans and claims consume the per-tick batch budget. A coin that cannot be processed waits or is quarantined alone; a burst of quarantines stops the process. Safe unlocked payout inputs are selected largest first. Existing claims get the first payment attempt; a batch failing its actual fee or funding check is reduced, and deferred claims do not block new claims | `fee-pct-rule`, `fee-no-estimate`, `circuit-breaker`, `happy-batch`, `fee-stuck-claim`, `fee-window`, `wait-window`, `fee-fragmented`, `fee-fragmented-progress`, `exit-breaker` |
| 5 | A restored wallet cannot find or spend its payouts | Payout to BIP86 `tr(coin key)`, derivable from the seed; the bark fork adopts the spent state, finds the payout and sweeps it | `web-journey`, `unclaimed-delegated` |

Process: one instance (Postgres advisory lock); the captaind schema version is allowlisted and checked every tick; the payout tx is verified (outputs, amounts, fee share, recipient deductions equal the full mining fee, change ours) before it is stored.

## Limitations

Quarantine leaves the claim recorded for review. Removing a row only makes a coin eligible for rechecking; it does not bypass any payment guard.

| Quarantine reason | Release evidence | Next action |
| --- | --- | --- |
| `undecodable vtxo`, `undecodable unclaimed input` | The original coin/round bytes decode with the supported version | Restore correct history or update the decoder; remove this quarantine and retry |
| `unparseable spender txid` | A valid recorded spender matching the exact on-chain outpoint | Repair the record from chain/round evidence; remove the quarantine and retry |
| `invalid exit path` | The stored path validates against the original anchor transaction | Repair the stored bytes or decoder from original round evidence; remove the quarantine and retry |
| `unclaimed output has no hArk participation hash` | The output's original hArk data identifies its participation | Restore/decode that data; remove the quarantine and retry |
| `unclaimed output's original input history is missing` | Matching funding transaction and unlock hash resolve all original input records | Restore the matching round/participation/input history; remove the quarantine and retry |
| `payout committed in local journal` | The original journaled transaction confirms | Keep the coin spent and retain the journal; the retry path settles the payment independently of quarantine. Missing Ark history requires matching backup/WAL before captaind restarts |
| Legacy `round partially unrolled by …` | Confirmed sweep on this coin's exact path | Startup removes these obsolete quarantine rows; the current path proof decides eligibility |

An unclaimed replacement whose original input has no confirmed sweep waits without being banned or claimed. An input already claimed by its owner cannot receive a second full payout through its replacement. A participation mixing exited inputs with swept inputs remains for accounting review: automatic partial compensation is not implemented. Its remaining input records must be retained to resolve the unpaid portion; deleting the participation is not settlement.

- **Waiting on fees** keeps the coin with the operator: no estimate or a fee share above the cap leaves it unpaid until fees fall. Unclaimed coins stay refreshable; already-claimed coins retain their payout obligation.
- **Confirmation floor:** with `grace_blocks` ≥ 144 above `sweep_min_confs` ≥ 100 (mainnet floors), a sweep made at expiry is already deep enough when the grace period ends; `sweep_min_confs` only matters for late sweeps.
- **Reorgs:** a payout is not re-checked after 6 confirmations; a deeper reorg that drops it goes unnoticed.
- **No RBF bump:** a payout stuck at a low fee is rebroadcast but not bumped.
- **Client side:** captaind does not tell wallets that a coin was paid out. A wallet must ask (`GetVtxoStatus`), find the payout at `tr(coin key)`, and sweep it. Until it does, the coin still appears in its balance and refreshes of it are refused.
- **Dependence on captaind's DB:** column names, `spend_state` values and the stored VTXO encoding are captaind internals; every captaind upgrade goes through the gate in `deployment.md`.

## Seed-only recovery range tested

`tests/regtest/seed-sparse.sh` funds the payout script directly at indexes 0,
257, 65,537 and 999,999, including two outputs at the last key. Plain Core
`scantxoutset` over `tr(coin_xprv/*)` with range `[0,999999]` found and a fresh
Core wallet spent all five. On Core 31 and the recorded 11,484-output regtest
UTXO set, the scan took 128.996 seconds; RSS was 90,824 KiB before and sampled
peak 998,148 KiB. A `[0,200]` scan found only index zero. Details and limits:
`tests/results/2026-10-07-seed-sparse.txt`.

The heir still needs a scan range; the seed does not reveal the highest used
index. These measurements do not promise discovery beyond 999,999 or describe
bark-web's automatic restore behavior. `unclaimed-delegated` separately tests
an actual sidecar payment followed by seed-only Core recovery and spending.
