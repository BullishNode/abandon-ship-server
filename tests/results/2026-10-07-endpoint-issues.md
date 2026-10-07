# RPC adapter: owner issues 1–3

Executed on the isolated `abandon-captaind-api` regtest stack, 2026-10-07.
Baseline adapter `5a8114e89d0dc7505501ab913ba2ea65f5e8b80e`; unchanged captaind
`5848679f84ac0efc67b3d7b2b618aa516e38450f`.

Local commits:

- `4a663f7df99f8b93a32f0794baf57b39bb007a37`: default info logging and tick counters.
- `a4aa5d15659e2efbc530fc747e9e4db6894a68c3`: grouped restored claims and raw-byte diagnostics.
- `4e57cd68f5c3f11dd77a829f7a85509c756b3400`: fresh replay-input startup launcher.

Final `cargo test --locked`: **10 PASS / 0 FAIL**; locked build, shell syntax and
Git diff checks PASS. No captaind API/DDL/dependency changes, pushes, VM/client
changes or other-stack mutations. No full-suite or production-readiness claim.

## 1. Logging

The before binary with `RUST_LOG` unset produced **0 bytes**, hiding a real
no-estimate warning. After: unset and invalid filters each emitted the warning
and exactly one info summary; `RUST_LOG=error` emitted neither. **4 observations
matched expectations**, including the before failure. Each summary recorded the
observed Core tip and zero candidates/claims/submissions/quarantines, duration and
success. The live payout case below additionally checked claims=2 before funding,
one successful recovery submission, zero submissions for an already-mined batch,
and claims=1/submissions=1 for the unrelated payout.

The canonical Core estimator still answered after 1,000 empty blocks, so that
setup did not reach the requested condition. The accepted no-estimate check used
a separate fresh Core 31 node with an empty estimator and wallet, against the
unchanged already-confirmed sidecar ledger. It made no claims or payments. Its
tip was zero; this was a filter/heartbeat check, not a funded chain scenario.
The initial Python filename shadowed `logging`; it was renamed before checks ran.

Counters count visited candidates, new handoffs and successful submission calls
(including retries); imports and confirmations are not new claims/submissions.
A positive quarantine count was not exercised in this increment.

## 2. Grouped recovery

**9 checks PASS / 0 FAIL** across the recorded crash/restore continuation:

- Created three actual 5M-sat boards, swept their exact roots, and claimed the
  first two with funding locked. Saved a real post-claim `pg_dump`.
- A forwarding proxy held the first `sendrawtransaction` before Core received
  it. SIGKILL returned **-9** after signed DB storage and the complete fsynced
  batch journal. The held bytes matched both rows and the journal; Core had no
  such transaction and its `testmempoolaccept` allowed those exact bytes.
- Restored the post-claim dump, retaining the independent current journal.
- With complete legacy journal records lacking raw bytes and no DB bytes,
  both versions stopped, exit 1. Before omitted the txid; after named the coin
  IDs **and** payout txid.
- Held the later claimed row with a real Postgres row lock. Before, an
  independent observer saw **signed/claimed** while recovery waited. After,
  it saw **claimed/claimed** until the grouped transaction could commit.
- A retained signed DB row repaired a legacy journal lacking bytes; the other
  claimed row joined the original transaction without broadcasting during export.
- Recovery broadcast original transaction
  `b973fb5b53834505ba21c205cd188f6621d2fbdfebd6035f5449d6fb54dc91e7` and confirmed both
  rows. Restoring the same post-claim dump after confirmation immediately restored
  confirmed state, with **zero** successful submissions and unchanged raw bytes.
- Each recipient appeared in exactly that one payout transaction. The third
  unrelated entitlement then claimed, paid and confirmed normally.

A separate real-backup clone started with two claimed rows and a third missing
row. **3 checks PASS**: normal imports recovered all 26 rows, every original txid
and raw transaction matched, and no submission occurred. Existing signed or
confirmed members and missing rows are valid recovery states; grouping updates
only the claimed members present in that pass.

Harness corrections are retained: the parent's reference `rpc-hold.py` returned
HTTP/1.0, which this RPC client rejected before signing. The tested private copy
uses **HTTP/1.1 plus Content-Length**. The next run performed the intended SIGKILL,
but its observer assumed unknown Core transactions use an HTTP error; Core sent
HTTP 200 with JSON-RPC error -5. The corrected continuation resumed the same
signed transaction. This was not one uninterrupted clean scenario run, and the
parent's original scripts are not claimed passing.

## 3. Native startup replay

A real pre-claim captaind backup contained all three later-paid coins and the
older 23 handoff receipts. Starting native captaind with that older 23-ID replay
file opened its public listener while all three later-paid inputs still read
`spendable`: **expected before failure**. No deliberate duplicate user refresh
was sent. This verifies stale-input exposure, not a second executed settlement.

The 12-line `contrib/start-captaind.sh` closes that deployment gap without adding
an API or marker table. Every start first exports current local DB/journal IDs,
then forces the existing native replay configuration to that output path. Export
failure stops before `exec captaind`, even if an old file exists. Writers must be
stopped and current state/journal preserved independently of the captaind backup.

**4 checks PASS / 0 FAIL**:

1. Core actually stopped: export produced all 26 IDs and native replay committed
   protection. Captaind then failed its Core requirement, exit 1, without opening
   the listener. The DB protection does not depend on Core availability.
2. Restore again with the stale file/configured path: launcher regenerated 26 IDs;
   the listener opened only with all three later-paid inputs already spent.
3. Export DB unavailable: launcher failed, preserving the old file but never
   opening captaind's listener.
4. Genuinely fresh Core/key/captaind DB/sidecar DB: exported zero IDs, replayed zero
   before workers, and opened normally with no VTXOs or claims.

Raw `captaind start` bypasses the launcher and remains unsafe with stale/omitted
replay input. If both the sidecar DB and journal are stale, neither can reveal
claims missing from both. Missing Ark transfer history still requires restoring
that history. Existing missing-ID/unfinished-round/atomic-import tests are prior
Bark evidence; they were not rerun here because the captaind patch is unchanged.

## End state and cost

**26 confirmed local claims / 26 captaind handoffs / 26 replay IDs**, empty mempool,
height 4095. All **21 public receipts / 25 outputs** matched Core fees. Canonical,
fresh and clone services are stopped; all volumes are preserved. Worktrees clean
apart from this result file before its evidence commit.

This increment adds/removes **56/19 Rust lines** and adds the 12-line launcher.
Adapter Rust versus frozen `6f40dbe`: **475 additions / 251 deletions**. Captaind's
756-line patch is unchanged. Logging adds one `getblockcount` RPC per tick; grouped
recovery commits the present claimed members once per transaction. No history/load
measurement was repeated; old timings must not be presented as current results.

Private exact evidence, manifests, binaries, dumps and corrected helpers:
`~/bark-integration-2026-10-01/captaind-endpoint-evidence/adapter/issues-parity/`.
Contains private ledger/key material; publish only this sanitized summary.
