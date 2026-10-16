# Public native budget observation proposal

This is a Track B deployment contribution, based on the deployed observer at
b7cacf0ecfdabd993d1dee74e2e0ad33b213809a. It has not been installed. The native
format reference is fa3a750a6349552cebbe512f8897110db7667ea0, tree
8d4a8f9400838ecc52e8badf00b89865bbebc9a8. Final native source compatibility must
be checked before installation. Track A owns the shared normal collector;
this contribution does not edit the common collector or simulator ledger.

The fixed request is `{"kind":"native_ordinary_fee_budget"}`. Extra arguments
are refused. The bridge invokes the fixed root-owned reader at
`/opt/abandon-simulation/native-budget-observation.py`, with isolated Python and
no arguments. Installation requires the bridge, helper and restricted sudo
rule to be reviewed and installed together by the Track B operator. No generic
privileged Python invocation should be granted to the observer account.

The reader inspects only the fixed own cap container through the local Docker
Unix socket, bounds its mount inventory, and accepts the exact named volume or
a canonical captaind bind beneath the owned deployment parent. It rejects
nested mounts and rechecks container/mount identity after reading. No caller
selects a path, container or RPC. The source binding is returned as a hash.
The initial default-volume assumption refused the real target bind before any
installation; this measured child supports the actual owned deployment.

The helper reads only `ordinary-fee-policy.json`, `ordinary-fee-opening.json`
and `ordinary-fee-budget` beneath the fixed direct-expiry witness directory.
Every directory and file open rejects symlinks. Files must be regular and
bounded (2,048 / 2,048 / 2,097,152 bytes). Metadata is checked across the complete
observation; concurrent changes cause refusal. The helper neither opens the
signed history nor initializes, locks, appends or loads the native budget.
The request has a 20-second overall deadline; the helper emits at most 16 KiB.
Missing, malformed or incompatible files mean unavailable, never zero spend.

The public projection reports the policy/opening/ledger byte hashes, public
network/genesis/server identity, opening costs, fixed external partition,
reservation sum/count, total charge and remaining ordinary allowance. Policy
and opening bytes must match the native canonical format and opening hash.
The append ledger follows the native positive decimal reservation format.
No private witness bytes, raw ledger rows, keys or configuration are returned.

For the declared adopted opening, actual prior miner spend is 4,985 + 879 =
5,864 sat. Native opening charge includes the separate 20,000-sat prospective
external partition: 25,864 sat, leaving 34,136 sat of the 60,000-sat signer
allowance before later reservations. These are different quantities. A full
ceiling is charged once before each new signed attempt; failed or confirmed
attempts do not replenish it. This observation must never book the 20,000-sat
partition as actual cost, duplicate the prior 4,985-sat carrier cost, or reset
the simulator's actual-spend epoch (5,864 spent / 54,136 remaining).

Whole urgent eligible cash is deliberately null in this disk operation. The
shared collector should join its existing bounded owning-watchman-wallet and
independent Core observations: current chain/identity, confirmed mature
unlocked whole UTXOs, actual unspent state and active input reservations. The
result is a candidate cash snapshot, not proof of reserve isolation. The native
signer must still exclude every actual ordinary selected input and preserve
at least 15,000 sat in remaining eligible whole inputs. One 72,345-sat input
cannot simultaneously fund an ordinary transaction and its urgent reserve by
counting prospective change.

Every response retains `runtime_loaded_identity_verified=false`,
`retained_signed_fee_coverage_verified=false`,
`urgent_selection_isolation_verified=false`, `complete=false` and
`qualified=false`. Disk bytes do not prove the running daemon loaded them,
that every retained signed version is covered, that the opening receipt is
authentic, or that a particular transaction preserves reserve isolation.
Those require separate native/startup and transaction evidence. The public
projection supplies facts for the existing gates; it grants no dispatch or
financial authorization.
