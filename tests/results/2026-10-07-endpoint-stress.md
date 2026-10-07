# Endpoint cutover and historical paging — 2026-10-07

Stopped-writer cutover: **8 PASS / 0 FAIL**. Actual direct sidecar rows covered
claimed, signed, broadcast and confirmed states; a killed startup import left
no partial receipts or public listener. Retry preserved bytes and settled all
eleven real obligations. Stock captaind and the configured 120-second ban wait
were used to create the source state.

Synthetic baseline: **30 PASS / 0 FAIL**; optimized repeat: **30 PASS / 0 FAIL**.
Each arm claimed only the eligible tail, preserved waiting rows, emitted no
transaction and exited without a tick error. Two repetitions per history/arm;
one per boundary/arm. These are claim-processing, not funded throughput tests.

| History | Direct seconds (repeat matrix) | RPC seconds before | RPC seconds after | Total SQL direct / RPC after |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 0.215 | 0.215–0.265 | 0.168–0.215 | 23 / 35 |
| 1,000 | 0.216–0.317 | 0.265–0.721 | 0.265 | 23–27 / 51 |
| 10,000 | 0.265–0.366 | 1.920–2.778 | 0.717–0.766 | 27 / 199 |
| 100,000 | 1.777–2.072 | 182.806–207.554 | 7.736–9.262 | 31 / 1631–1639 |

Fixed synthetic backlog: 16 waiting aliases before one eligible tail. One
unchanged real waiting candidate sorts after the tail in every fixture: 18
database candidates in each history case, of which only the tail can claim.
Boundary labels count waiting aliases before that tail. History boundaries
255/256/257 and waiting-page boundaries 255/256/257/513 all passed in both
matrices. Direct fixture bans were already 121 seconds old under the unchanged
120-second wait. Funding stayed locked. Copied VTXO payloads and synthetic row
IDs are database-only aliases, not independently funded entitlements.

At 100,000 receipts, the original RPC spent 176,530–200,389 ms in logged Postgres
work; the new primary-key reader eliminates repeated whole-history sorting.
The receipt cursor now uses ID only and expiry zero; candidates keep expiry/ID.
All receipts are still scanned every tick. No table, index or dependency was
added. This does not establish a throughput improvement over direct DB.

| 100k repeat arm | Postgres duration ms | Captaind / local SQL | Sidecar max RSS MiB | Captaind peak RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| rpc #1 | 960.1 | 1618 / 13 | 103.3 | 43.6 |
| direct #1 | 365.1 | 31 shared | 76.0 | 23.7 |
| direct #2 | 526.8 | 31 shared | 75.9 | 24.1 |
| rpc #2 | 1372.0 | 1626 / 13 | 101.0 | 43.5 |

Resource samples: sidecar GNU time and 20ms captaind RSS / private Core/Postgres
cgroup memory sampling. Cgroup memory includes cache and is not process RSS.
SQL totals include both captaind and sidecar and ordinary captaind workers during
the tick; query timings include logged parse/bind/execute durations. Full SQL
logging adds overhead. Stock captaind is the pinned published image; patched
captaind and both sidecars are debug builds. The gate waits for no compiler and
less than 70% host CPU before each sample, not throughout it. Warm caches,
alternating order and other host work prevent capacity or tail-latency claims.

| 100k repeat arm | Postgres cgroup baseline → peak MiB | Core cgroup baseline → peak MiB |
| --- | ---: | ---: |
| rpc #1 | 1221.8 → 1232.0 | 134.8 → 135.1 |
| direct #1 | 1363.7 → 1427.2 | 133.6 → 135.1 |
| direct #2 | 1367.3 → 1432.9 | 134.6 → 134.8 |
| rpc #2 | 1438.5 → 1457.3 | 134.1 → 135.3 |

Preserved setup failures (not settlement failures): missing stock entrypoint;
compiler activity exceeded the first quiet-host deadline; incorrect cgroup
controller lookup. Each was corrected before the completed matrix. Per-arm
timeout: 900 seconds. Assertions, manifests, logs and failed attempts are retained.

The parent’s new Core input-selection fix is intentionally not in either pinned
benchmark payout binary. Existing fragmented-wallet starvation remains an open
parity issue. Funding-locked history runs cannot validate its resolution.

Full common adapter suite, sustained funded load, foreground contention, soak,
cold-cache qualification, message-size limits and rollback after new RPC claims
remain unrun. This is not a production-readiness or mainnet-capacity certificate.

Stop-after-first-page mutation: **intended tail assertion failed; restored implementation passed**.
Receipt committed behind cursor: **3 PASS / 0 FAIL**.
Actual funded board after query change: **4 PASS / 0 FAIL**.

The first late-receipt attempt stopped in setup because its synthetic SQL update
omitted the table trigger’s required updated_at; no daemon had started. That
SETUP_FAIL and its logs are preserved separately from the passing retry.

The first funded-after-paging attempt waited for a confirmed ledger hint before
mining a sweep already in Core’s mempool. No sidecar tick ran. That SETUP_FAIL
is preserved; the corrected observer checks the exact mempool input first.

Revisions: captaind `5848679f84ac0efc67b3d7b2b618aa516e38450f` on base
`3ab429866`; adapter source `faee8ad` on frozen combined baseline `6f40dbe`.
No payout selection change was made for this increment. Captaind library tests:
**160 PASS / 0 FAIL / 12 ignored**; explicit endpoint DB tests: **12 PASS / 0 FAIL**.
Adapter unit tests: **8 PASS / 0 FAIL**. Workspace typecheck/build and four
prechecks passed. No full common adapter suite is claimed.

Artifacts remain under the private experiment evidence `adapter/stress/`: drivers,
original setup failures, per-arm manifests/results, resource samples and compressed
SQL logs. Sanitized combined result SHA256:
`900300911999c1a165b09e93a44ee9d505a27f9d9bd7e5669bb76104811925a5`.
The enclosing evidence directory also contains private regtest seeds/wallets/dumps;
do not publish it wholesale. All changes and service actions stayed isolated.

Core 31.0.0 / Postgres 16.13; matrix height 1635. The final funded check advanced
the chain and spent the waiting board. Another matrix now requires a fresh
matching waiting/base fixture; no automatic Core reset or snapshot is supplied.
