# Isolated endpoint adapter — 2026-10-07

Scope: experimental sidecar `faee8ad` (adapter `fbf68b3`, frozen combined baseline
`6f40dbe`) with captaind `55790a798` (endpoint `8f5a979`). Private
`abandon-captaind-api` stack only; stock watchmand and Core 31. Nothing was pushed,
merged or deployed. Production regtest scenarios were not run against this adapter.

| Executed check | Exact final result |
| --- | --- |
| Frozen sidecar baseline unit tests | 7 PASS / 0 FAIL |
| Fee equality regression before fix | 7 PASS / 1 expected FAIL |
| Final adapter `cargo test --locked` | 8 PASS / 0 FAIL |
| Remove exact-vout comparison | 0 PASS / 1 expected FAIL |
| Captaind ordinary library tests | 160 PASS / 0 FAIL / 11 ignored |
| Explicit isolated endpoint database tests | 11 PASS / 0 FAIL |
| Captaind prechecks and workspace tests/examples typecheck | PASS, exit 0; existing warnings |
| Adapter examples/build | PASS, exit 0 |
| Missing-ID, pending-round and valid startup barrier | 3 PASS / 0 FAIL |
| Local write failure, actual payout, fee and retry | 5 PASS / 0 FAIL |
| Lost claim-fetch reply, signed-before-journal failure, RPC-down retry | 6 PASS / 0 FAIL, including partial batch/fee assertions |
| Actual captaind + sidecar pg_dump/pg_restore | 5 PASS / 0 FAIL |
| Exact-path partial-exit chain proof | 4 PASS / 0 FAIL |
| Fresh seed-only Core wallet finds and spends payout | 3 PASS / 0 FAIL |
| Fee selection, fragmented actual fee deferral, funding recovery | 5 PASS / 0 FAIL |
| Identical-fixture direct DB/RPC claim comparison | Four target claim assertions PASS; all three new claim IDs match |

Actual first payout: 59,520 gross → 58,824 net, fee 696 sat, transaction
`3dd354babe409a4960212fe4a5ee24af13e94fb748cef519f6826aff474e3a7f:1`.
The restored batch retained transaction
`57795513c1c49241b374076303fc552d61c62ec3feb49d18ecb5938fedf13e4d`.
It paid three outputs, each deducting 251 sat, total actual fee 753 sat.
A fresh Core wallet recovered output 2 from only the seed-derived descriptor
(range 0..200, scan 0.069 seconds) and spent that exact output in
`2cd778f936569b393a4c7bb5820f4a66ee5dddbbc690cd663c7a79c9203ea702`,
fee 300 sat. This is neither a high-index benchmark nor a browser test.

Fee fixtures: 19,670-sat entitlement stayed unclaimed at the 1% rule while a
99,670-sat entitlement paid 99,175 sat. With 45 small funding UTXOs and the 4%
rule, the small entitlement was claimed but failed the actual fee check. Normal
funding then paid the same claim 19,205 sat, fee 465 sat. Seven total claims ended
confirmed, with seven permanent captaind receipts.

The benchmark holds funding locked and compares the same three eligible claims,
two prior payouts, fee estimate, chain and wallet. Direct DB uses two ticks with
zero configured ban wait: 0.125 + 0.299 s, 81 SQL statements, 39 Core calls.
Adapter: one tick, 0.380 s, 17 local SQL + 36 measured captaind SQL statements,
21 Core calls and 10 admin RPCs. Debug builds, one sample, local warm services;
this is a call-path measurement, not a throughput claim.

Earlier failed attempts remain in evidence: an unloaded Core wallet, empty real
fee estimates after long mining, fixture assertions assuming fewer pre-existing
claims than the database contained, missing example hex import, missing test
Config field, and initial DB error assertions checking only anyhow's outer
message. The final retry verifier includes all selected IDs; it did not rebuild
the already-broadcast transaction. Empty fee estimates correctly produced no new
transactions. Full-suite/mixed-batch actual-fee starvation/shared-key/client UX
regressions remain outside this experiment's executed coverage.

Sanitized summaries, raw test outputs, patches and binary hashes are under
`~/bark-integration-2026-10-01/captaind-endpoint-evidence/adapter/`.
Only `sanitized-results.json`, source patches and test summaries are suitable for
sharing. The enclosing evidence directory also contains private regtest seeds,
wallets and database dumps; do not publish it wholesale.
