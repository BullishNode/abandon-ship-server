# RPC adapter: static exact-fee receipts

Executed 2026-10-07 on the isolated `abandon-captaind-api` regtest stack.
Baseline adapter `544a363861772ec36b44162b42668e2b28e0f387`; receipt implementation
`1cabd2de603e3b4504a3806fb53ecf3bdf8a23c7`; restored-batch fix
`7b2b675c52b5e85460542273dd228a9ea9302bc8`. Captaind remains the published
`5848679f84ac0efc67b3d7b2b618aa516e38450f`; no API, DDL or dependency changes.
Commits remain local. No client worktree, other stack, image tag or VM changed.

## Results

- Initial shared receipt draft: **9 unit PASS / 0 FAIL**, locked build PASS.
- Final adapter: **10 unit PASS / 0 FAIL**, locked build PASS, diff check PASS.
- Fresh shared-key payout/recovery sequence: **8 checks PASS / 0 FAIL**, exit 0.
  Three actual Ark payments to one reused receiving address created 5M, 6M and
  7M-sat entitlements. The first batch selected only the first two; its receipt
  reports **10,999,505 net / 495 fee**, not all 18M in that key's history. The
  later transaction reports **6,999,505 net / 495 fee**. Core independently
  confirms input-minus-output fee equals all recipient deductions.
- Actually restoring the independent state DB to a pre-claim `pg_dump` removed
  both first-batch rows while preserving captaind handoffs and current journal.
  An unavailable admin endpoint did not lose/change the original payment; fee
  metadata remained unavailable. Export reported failure without changing any
  payout, quarantine or journal bytes. Normal reconciliation recovered the same
  transaction/raw bytes and byte-identical receipt; both claims confirmed.
- Replacing the auxiliary receipt directory with a regular file did not prevent
  the third entitlement's payout. Restoring the directory and retrying with the
  admin endpoint unavailable generated the correct receipt, preserving original
  raw transaction, txid and journal. All three claims confirmed.
- Historical export initially regenerated **16 transactions / 18 outputs**.
  Every net amount and summed deduction matched Core. Database rows, quarantine
  and journal were unchanged. Files matched the previous complete export byte
  for byte.
- A real older DB backup plus the current journal exposed a draft exporter bug:
  **exit 0** despite **14 known journal transactions omitted**. After correction,
  **exit 1**, all 14 missing transactions reported, **2 independent valid files
  regenerated**, and the payment/journal/quarantine snapshot unchanged. A legacy
  partial-round quarantine sentinel also remained unchanged.
- Partial import retry exposed a second bug: a confirmed batch member caused a
  restored member to remain `signed` after an injected local INSERT failure was
  removed. Three successful retry ticks still left the receipt missing. Before:
  **FAIL** (observer recorded the expected failure and exited 0). After the fix:
  **PASS**, all three restored rows confirmed and the shared receipt reappeared.
  The same failed fixture then recovered with the fixed binary. A new unit test
  covers clearing a prior chain-confirmation hint until every member is present.
- Final read-only export with an unavailable admin URL: **PASS**, exit 0,
  payments/quarantine/journal/mempool unchanged. Local static HTTP served the exact
  JSON and returned 404 for missing files and the unserved private journal.
- Final chain check: **19 receipt transactions / 22 recipient outputs PASS**.
  Two eligible sender-change entitlements were also settled by the normal policy:
  4,890 and 3,890 gross, 4,593 and 3,593 net, exactly 594 sat total mining fee.
  **23 local claims / 23 captaind handoffs / 23 replay IDs**, all confirmed; empty
  mempool. All private services stopped and all volumes preserved.

## Fixes and scope

The public format is `{txid, outputs: [{vout, amount_sat, fee_sat}]}`. Files are
written to a temporary path, fsynced, renamed and directory-synced. Parent
creation is synced; a cached receipt retry also syncs its directory. The module
comes from the parent's static-receipt draft, with those durability details and
an interrupted-replacement unit assertion. Normal receipt errors only warn.

Export enumerates the union of local transaction IDs and read-only journal IDs,
continues valid independent exports, and fails overall for unavailable metadata.
It executes before journal reconciliation or legacy-quarantine cleanup. No new
receipt store is added to captaind. The restored-batch fix recomputes confirmation
from all journal members rather than treating any one confirmed row as the whole
batch. This keeps historical confirmed batches out of Core retry calls while
allowing a later import to finish.

This increment adds **192 / removes 5 Rust lines** against the funding baseline,
including receipt and journal tests. Total adapter Rust versus frozen `6f40dbe`
is **423 additions / 236 deletions**. The captaind patch is unchanged. Historical
load timings predate this increment; no new performance claim is made.

## Fixture failures and limits

Initial setup assumed JSON/string address output, then the wrong regtest address
prefix, then `amount` instead of `amount_sat`. The three already-received coins
were retained and setup resumed without another payment. The initial partial
retry trigger targeted expiry order instead of receipt-ID order; corrected
before/after fixtures use the actual order. A final idle-state assertion initially
used the normal lower minimum and correctly paid the two sender-change coins;
those payments were preserved and confirmed, then the assertion used the same
minimum as its fixture. Failed scripts/available logs remain in private evidence.
These setup failures are not counted as implementation passes.

No new browser/native-client journey was run here; the parent separately verified
published Bark/client support for this format and unknown fees. Receipt loss never
makes chain funds unspendable. Missing historical files require explicit export.
The raw journal does not contain gross entitlements: after row loss, normal
captaind-receipt reconciliation must restore them before an absent receipt can be
reconstructed. Core must retain access to funding transactions. No new full RPC
suite, large funded load, soak, Signet or mainnet qualification is claimed.

Private source scripts, manifests, binary hashes, actual backups and exact results:
`~/bark-integration-2026-10-01/captaind-endpoint-evidence/adapter/static-receipts/`.
That directory contains private wallet/ledger material; do not publish it wholesale.
