# Signet rounds-wallet geometry window

This is an operator test utility, not a change to captaind or a mainnet tool.
It reuses the fork's native BDK wallet, signer and nursery/database APIs. It
only pays scripts owned by the rounds wallet and keeps every existing payout,
coin, key link and fallback record. No top-up or direct balance edit is used.

## Build and stage

Use an independently verified clean fork checkout at
`8f2286ee7e1631b0ceccd1631ec3b728a1bb0a52`. Link it as `sdk` in this directory
(the ignored symlink points outside the deployment checkout). Then build:

```sh
cargo build --locked --manifest-path rounds-wallet-maintenance/Cargo.toml
```

The usual fork PROTOC/CMAKE build environment applies. Record the source and
binary hashes. Copy the binary to a root-owned, non-writable executable under
`/home/ubuntu/abandon-signet-fallback/tools/` on the owned target. Copy the
controller as `rounds-wallet-window.py`, and the existing freezer as
`freeze-signet-upgrade-8f.py` in the same tools directory. Staging or a build is
not a live fragmentation pass. A parent utility that persisted nursery and
wallet spend state separately was corrected before any live execution.

## Declare and coordinate

The simulator owner supplies a fresh actual pause acknowledgment with global
client/recovery/funding in-flight and unknown counts zero, the unchanged target
identity, and the planned input-conflict/reuse profile. The fork lead declares
`run_id`, `head`, exact qualified native binary path/hash, public `rounds_xpub`,
the complete confirmed input set/value, output count (1–32), chunk value,
integer fee rate, maximum fee, and the verified public descriptor range limit.
The utility refuses unknown/missing inputs, an unexpected wallet, unconfirmed
wallet outputs or any active nursery transaction. It verifies each input's
value/script/confirmation against Core and enforces non-dust own outputs.

Coordination JSON must carry `simulator_dispatch_paused=true`,
`inflight_actions=0`, `unknown_actions=0`, `recovery_inflight_or_unknown=0`,
`funding_inflight_or_unknown=0`, timezone-aware `observed_utc` less than ten
minutes old, `target_head`, `target_vm` and `target_identity.image_digest` /
`server_pubkey`. This is real operator coordination, not an invented test flag.

## Three explicit phases

From the target, invoke the controller with `--run-id`, `--request` and
`--coordination`. The request is immutable between phases. Never overwrite a
run directory, container or intent to obtain a green result.

1. `prepare`: refuses active nursery work before stopping services, uses the
   existing freezer to verify identity, stop all owned writers and create
   private database/volume backups, then runs the native plan in a named
   restricted container. It leaves the services stopped. The plan derives in
   memory only; no signature, persistent wallet change or broadcast occurs.
2. `commit`: requires the successful prior plan and a fresh actual pause
   acknowledgment. It rechecks stopped writers and unchanged nursery history.
   Native signing persists the spend and derivations atomically with an
   `internal` nursery row, then broadcasts those exact stored bytes. The
   controller checks the immutable row, raw Core transaction, outputs and
   input-value = outputs + fee. It starts the same preserved captaind container,
   requires healthy guard/runtime startup, then starts the prior dependents.
3. `resume-unchanged`: for a failed/canceled plan only. It refuses any new
   nursery transaction, signed intent or still-running native container. It
   restarts the preserved containers without signing. This earns no
   fragmentation credit.

Native containers are retained, their names and IDs recorded before execution.
An unknown or failed invocation leaves the target frozen. Query that exact
container and reconcile its intent/database/Core state; never start a new
signing attempt. If a transaction was committed, retry only its stored bytes.
Do not use the no-spend resume phase to abandon committed work or erase it.
Backups/intents/logs that contain wallet data stay private on the target.

The stopped-writer check covers the owned containers and any other active
container sharing their wallet volumes. The existing honest-host rule still
requires one server/wallet writer and forbids an uncoordinated host daemon.
This utility does not introduce a captaind singleton lock.

## Qualification remaining

Compilation and controller syntax/help checks establish preparation only.
Execute the declared window on signet, independently verify all self-transfer
scripts from the public descriptor, wait for confirmation, run the simulator's
actual concurrent refresh/offboard/expiry profile, then restore the declared
geometry using a separate one-output self-transfer and fee declaration. Keep
both fees in the operator capital ledger, and reconcile every client payout.
No signet execution, input-conflict result or restoration is implied by these
files. Every existing security and payout-source guarantee remains required.
