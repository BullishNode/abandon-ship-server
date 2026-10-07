# RPC adapter shared funding — 2026-10-07

Base: published adapter `bd78fe66d77efcc8fc271e0fc41c441e483465c6`, pinned to
captaind `5848679f84ac0efc67b3d7b2b618aa516e38450f`. The increment imports
`stages/148d` funding and batch-error handling: **24 additions / 2 deletions in
2 Rust files**. It adds no dependency or RPC operation. Exact whole-fee checks,
claim receipts, journal recovery and stored-transaction reservations are retained.
There is no recipient-budget `max_tx_weight` option.

| Executed check | Result |
| --- | --- |
| Baseline adapter unit tests | 8 PASS / 0 FAIL |
| Updated adapter unit tests | 8 PASS / 0 FAIL |
| Locked build | PASS, exit 0 |
| Batch1, old binary on actual fragmented funding | Expected SETTLEMENT_FAIL: later claim unpaid after 3 ticks |
| Batch1, new binary on the same claims/funding | PASS, 6 reported checks |
| Batch2, old binary on actual fragmented funding | Expected SETTLEMENT_FAIL: later claim unpaid after 3 ticks |
| Batch2, new binary on the same claims/funding | PASS, 6 reported checks |
| Rejected signed payment + actual independent DB restore | PASS, 8 reported checks |

Each progress fixture used 32 confirmed 2,000-sat inputs, an earlier 59,670-sat
claim, a later 29,670-sat claim, a 3% fee limit and one additional confirmed
31,000-sat input. Normal wallet funding stayed locked. Old ticks exited normally
without infrastructure failure; both unpaid claims remained durable, with no
signed transaction. Those are intended progress failures, not setup failures.

With the new binary, the smaller claim paid using the suitable single input while
the larger claim remained unsigned. Restoring normal funding paid the same larger
claim. Both batch limits produced exactly these Core-verified amounts:

| Entitlement | Gross sat | Net sat | Actual mining fee sat | Inputs |
| --- | ---: | ---: | ---: | ---: |
| Earlier large claim, after funding returned | 59,670 | 59,205 | 465 | 1 |
| Later small claim, while large claim waited | 29,670 | 29,175 | 495 | 1 |

The rejection test created two actual 4,994,900-sat board entitlements. It took a
real `pg_dump` before signing A, forced journal append failure after signed storage,
and made Core reject A's exact transaction with a negative fee priority. Retrying
with admin RPC unreachable repaired the journal and reserved A's input. A real
`pg_restore --clean` restored the independent sidecar database to claimed/no-txid;
the current journal and captaind's permanent receipt remained intact. This is a
sidecar-only restore, not a new paired-captaind restore test.

The test explicitly cleared A's volatile input lock. The next tick reconstructed
it before selecting B's distinct funding input. B paid while A stayed rejected.
Lifting rejection let A rebroadcast its original bytes/txid with admin RPC still
unreachable. The expected admin error blocked later tick work; it did not block
that durable rebroadcast. Both payouts deducted their actual 495-sat mining fees,
confirmed, and preserved every prior settled row. Final canonical state has
18 confirmed payouts, 18 receipts and 18 exported IDs.

No case was interrupted. A temporary instruction stopped private daemons before
any case began; regtest-first testing then resumed. Volumes were preserved.
No Signet/mainnet action, publication or merge was performed by this increment.

Full shared-suite parity, an oversized Core-default weight case and performance
measurements of this new funding binary remain unrun. Earlier history timings
belong to their recorded previous binary; they are not rerun results.

Private evidence: `captaind-endpoint-evidence/adapter/funding-148d/`, containing
source/binary manifests, ordered events, exact before/after results and raw logs.
The source drivers are retained there. Database dumps and wallet seeds are private.
Do not publish that directory wholesale. Sanitized result SHA256:
`0aa3173d91fc663284a6db8844134797027812479adec4d50cd0d2711f9da285`.
