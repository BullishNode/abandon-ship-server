# Signet maintenance preparation

These fork-lead utilities operate on the owned fallback stack only. They do
not change the simulator or the original signet stack. Private dumps and wallet
archives remain on the target with owner-only permissions.

- `audit-database-restore.py OUTPUT`: exports a consistent live snapshot and
  restores into a new audit database; compares every table and the schema.
  The only schema normalization is PostgreSQL's validated paired dump nonce.
  `--frozen --archive PATH` audits the actual saved archive and sequence values
  after verifying all owned writers are stopped.
  It never replaces the live database or starts captaind. Sequences under live
  writes and independent history are outside the live-snapshot qualification.
- `freeze-signet-upgrade-8f.py --coordination JSON --output DIR`: requires a fresh
  actual simulator pause, zero wallet actions/unknowns and zero global recovery
  uncertainty, verified target identity and staged images. Stops owned writers,
  verifies the old PIDs are gone, waits for detached database commits and backs
  up the frozen database and wallet volumes. Preserves nursery transaction
  bytes and settlement identities. It installs no witness and starts no service.
  `--require-pending-payout` additionally requires declared real pending payouts;
  an ordinary empty-nursery upgrade earns no in-flight qualification credit.
- `prepare-payout-witness.py`: converts an independently audited inventory into
  native witness files in a new private directory. Formatting alone is neither
  complete-history proof nor permission to install or start a daemon.
- `fee-estimate-provider.py`: temporary signet-only Core estimate response
  control for the separately declared spike window. Read
  `FEE_ESTIMATE_PROVIDER.md` for payout/sweep separation, authenticating the
  private endpoint, fresh coordination, and its explicit fee-budget limits.
  `check` is read-only and opens no listener; installation alone changes no
  daemon's estimate. Its ten control tests do not qualify a live window.

Run only in a declared maintenance window. Preserve failed runs. A failed
post-stop check leaves services stopped for investigation; never automatically
restore an earlier database or initialize an empty witness. Keep the independent
witness outside the database's restore boundary. Native startup must validate
it before workers start. The full operator-window order and release gates are
in the goal handoff; these utilities do not replace them.

Qualification: the isolated live signet snapshot audit passed on 2026-10-13,
with all 43 tables and 17,605 rows matched. The actual frozen archive also
passed with 17,609 rows and all 20 sequence values; its initial nonce-only comparison
failure is preserved. Freeze and witness utilities have syntax/help checks;
this publication does not claim an executed upgrade or restore-guard journey.
