# Captaind endpoint comparison

This branch pairs with `abandon-ship-bark-captaind-api` and uses its generated
private admin client. The Cargo paths are local experiment dependencies. The
existing direct-database deployment and regtest scripts are not adapter scripts.
The executed isolated results and limitations are recorded in the external
`captaind-endpoint-comparison-2026-10-04.md` report.

Apply `migrations/0001_sidecar.sql` to a separate state database and captaind's
`contrib/expiry-settlement.sql` to its database. `captaind_url` is the existing
private admin listener. The sidecar never connects to captaind's database. Its
single `Exchange` RPC pages candidates/claims, obtains exact-outpoint spender
hints and atomically hands off eligible entitlements. Captaind's VTXO lock and
unfinished-round check replace the sidecar ban and timed wait. Chain validation,
fee selection, signing, the payout ledger and transaction journal stay here.

A committed captaind receipt survives a lost response or local insert failure.
Every tick scans receipts from the beginning and imports unknown obligations.
Before building any new transaction, every local ID must have a remote receipt.
Missing remote IDs stop the process. Locally durable signed transactions are
reconciled and retried first, even if the admin RPC is unavailable. All replicas
must use the same state database and journal; two independent ledgers must not
consume one receipt set.

## Restore

Stop all captaind, watchmand and sidecar writers. Preserve the current journal
and state database independently of the restored captaind backup. Run:

```
abandon-ship-server config.toml --export-settlement-ids /path/settlements.ids
```

This closes the local signed-before-journal window and atomically exports all
local claims plus journal IDs. Set captaind's top-level `settlement_replay_ids`
to that current file before restarting. Its import commits before any worker or
listener. Missing VTXOs, confirmed exits, conflicting Ark spends and unfinished
participations stop startup. Restore the corresponding captaind history or
resolve the recorded round offline; payout IDs cannot reconstruct lost transfers.
An omitted or stale export is not automatically detected before startup.

Then start watchmand and the sidecar. Remote receipts recreate missing local
rows; the independent journal reattaches their original signed transactions.
Never discard the journal or start a second payout ledger to bypass an error.

## Existing direct-database installation

Cutover is a stopped-writer operation, not a rolling mixed deployment:

1. Back up captaind, the entire sidecar schema and the current journal.
2. Copy existing `sidecar.payout` and `sidecar.quarantine` tables to the new state
   database, preserving raw transactions, states, amounts and addresses. Point
   the adapter at the same current journal. Do not copy or continue the ban loop.
3. Install captaind's receipt table, export every copied claim/journal ID and
   configure startup replay. Existing unclaimed bans may expire normally.
4. Start patched captaind, then watchmand and one adapter. Check replay count,
   reconciliation and original transaction IDs before opening normal operation.

The isolated cutover rehearsal uses stock captaind and actual direct-DB claims
in claimed, signed, broadcast and confirmed states. Killing the startup import
at an observed row lock rolls back its receipts before the listener opens.
Retry preserves the original signed bytes, refuses refresh and settles the
remaining obligation. Repeating the export is idempotent. Do not alternate
implementations after cutover without another stopped-state reconciliation;
rollback to a stale captaind backup is not supported by this procedure.

## Fees and receipts

Core subtracts payout fees from recipient outputs. The sidecar also requires
summed recipient deductions to equal the entire reported mining fee and checks
each output's percentage/dust limit. Multiple selected coins sharing a key share
one output. Gross for that output is the sum of its actually selected claims.

This branch does not deliver gross/net/fee receipts to clients. Seed recovery
finds and spends the on-chain net value; it cannot infer the original entitlement
amount or fee from historical spent coins sharing that key.
