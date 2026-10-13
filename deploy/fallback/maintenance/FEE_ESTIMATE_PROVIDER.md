# Temporary signet estimate control

`fee-estimate-provider.py` supplies the missing control for the required fee
window. It is an operator test utility, outside captaind/bark production code.
No live daemon is pointed at it by installing the file.

Payout mode changes only a successful single `estimatesmartfee(6, economical)`
reply; sweep mode selects `estimatesmartfee(3, economical)`. Other calls, batches
and node error responses retain their original status/body. An upstream call is
forwarded once, never retried after a lost response. After the declared interval
the provider forwards the original estimate again. Baseline Core estimates must
still be captured independently: an injected response is not a market estimate.

The provider requires the official signet genesis, a fixed private IPv4 backend,
a protected cookie file and a private/loopback listener. Auth is not journaled;
request IDs, bodies, seeds and payment preimages are never journaled either.
The journal records only control provenance and estimate observations. There is
no change to monetary policy, transaction construction or payout identities.

`check --request PROFILE.json` performs two read-only backend identity probes
and exits without a listener or estimate override. This preparation check is
allowed before an operator window. `serve --request PROFILE.json --coordination
PAUSE.json` requires a fresh actual global simulator pause, zero client,
recovery and funding in-flight/unknown counts, and the declared shipping8f
target. The fork lead independently verifies those receipts; the file itself
is not evidence that dispatch is paused.

The profile supplies target_vm, head, run_id, backend_host/backend_port,
cookie_file, estimate_mode, sat_vb (integer1–500), seconds (1–600),
listen_host/listen_port, evidence_directory, operator_fee_budget_sat,
budget_enforced_by_proxy=false and signed_transactions_survive_window=true.
Choose actual private endpoint addresses and independently declare all affected
coins, before/after configuration hashes, rate, timing and capital bounds.
Do not use a sample budget as permission to spend it.

**This provider does not enforce a fee budget.** Before activation the lead must
bound native exposure from the actual eligible cohorts and pending payouts;
monitor fees and signed commitments, including operator CPFP. If a profile
cannot fit its declared budget, it must not run. Restoring the estimate does not
cancel or re-sign a transaction already committed at the high estimate. Preserve
those nursery/witness identities and reconcile them through confirmation.

Root must preserve the ordinary operator-window order and fresh pause protocol.
Start one retained provider process, record its exact PID/command/ready receipt,
then in the declared window point only the relevant owned daemon at it while
preserving its private auth and other configuration. Verify the native request
in the estimate journal and actual raw transactions. Restore the exact native
URL/configuration, verify ordinary provider/target health and retained payouts,
then stop only the recorded provider PID. Keep evidence and failures. A missing
ready reply is an uncertain process outcome to inspect, never permission to
launch a duplicate. The original signet stack and simulator remain untouched.

Ten control tests passed: payout/sweep separation and automatic expiry;
authentication; original transaction-error bytes/status; no transport retry or
fabricated success; batch passthrough; secret-free journal; wrong-chain/public
endpoint refusal; and stale/nonzero/malformed coordination refusal. These are
control tests on local HTTP fixtures, not live signet payout-spike qualification.
