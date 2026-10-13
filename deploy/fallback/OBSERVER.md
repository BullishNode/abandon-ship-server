# Read-only simulation observer

`server-observer.py` implements the existing forced SSH observer command. It
accepts fixed public database inventories, a public rounds-wallet descriptor,
and finite log tails. It accepts no arbitrary SQL, RPC, shell command or path.
It does not open a client wallet, change server state or restart a service.

Install the script at `/opt/abandon-simulation/server-observer.py`. Preserve the
existing observer SSH and sudo permissions. Run each request through the existing
observer connection with the JSON request on standard input.

## Operator change ownership

Install the operator's independently declared public ranged descriptor beside
the script as `rounds-wallet-descriptor.json`. Include the network, genesis,
server public key, wallet kind, descriptor with checksum, derivation range and
capture timestamp. Export the public BDK descriptor, never a private descriptor
or seed. Do not commit a deployment's descriptor to this repository.

Request:

```json
{"kind":"rounds_descriptor"}
```

The reply carries `declaration`. Derive the specified scripts independently to
check operator change outputs. A change beyond the declared range needs an
updated declaration. The descriptor does not prove customer ownership, payout
eligibility, fees or completeness of the transaction history.

## Payout and sweep logs

Requests:

```json
{"kind":"logs","service":"captaind","seconds":300,"tail":1000,"max_bytes":262144}
{"kind":"logs","service":"watchmand","seconds":300,"tail":1000,"max_bytes":262144}
```

Only these two services are accepted. Limits are 1–86,400 seconds, 1–1,000 tail
lines and 4,096–262,144 source bytes. Defaults are 300 seconds, 200 lines and
65,536 bytes. Each finite Docker request has a 20-second deadline. A bound stops
only that request's child process. Named credentials, preimages and seed text
are redacted.

The reply includes `lines`, UTC window bounds, `source_bytes`, `truncated` and
`timed_out`. `complete` is always false: a bounded tail does not establish full
event coverage. Record gaps and bounds, retain overlapping captures and
deduplicate by service and complete event identity. Capture often enough to
cover tick summaries and waiting reasons; do not infer a successful tick from
an empty or truncated reply.

## Deployed release and preserved payout accounting

For the owned 8f signet upgrade, also install `release-observation.py` beside
the bridge, root-owned and not writable by the observer. Its paths are fixed;
the caller cannot choose a backup, executable, query, witness directory or
transaction. It uses ubuntu's existing administrative permission to read the
private witness, returning only public identity, counts and file commitments.

```json
{"kind":"release_identity"}
```

The versioned receipt checks native versions and hashes, hashes the running
daemon executables, binds the actual image/container/start time, reads the
mounted ordinary/pool lifetimes and payout policy, and compares all frozen
upgrade transaction bytes and immutable settlement destinations/fees with one
repeatable-read database snapshot. It also compares the current witness records
against that snapshot, without returning raw witness contents. Mismatch arrays
must be empty before treating preservation as satisfied.

The guard startup statement is grounded in the verified native `start` process,
payouts enabled, healthy administrative RPC, and the source's pre-worker witness
validation. It is not a claimed log event or a destructive restore test. The
frozen baseline remains trusted operator evidence. Native witness files stay
private, and the operation never initializes or repairs them. Service/config
changes during observation fail the request; retry a failed read, never infer
that a service stopped. Baseline, witness, output and database deadlines are
bounded; this upgrade-specific receipt does not replace campaign inventories.

## Validation

The fixed `pool_status` operation takes no arguments:

```json
{"kind":"pool_status"}
```

It reports unspent pool coins beyond the configured pre-expiry margin, grouped
by funding anchor. The confirmed subset requires Core to report that anchor
unspent with confirmed value covering the remaining pool group. Server key and
chain identity must match the operator's public descriptor declaration. At most
32 eligible anchors and 32 confirmed rounds-wallet outpoints are inspected; a
larger inventory refuses rather than silently returning a partial total.

`complete` applies only to this bounded pool snapshot. It does not mean the
customer-liability ledger is complete or reserve the coins against a concurrent
pool consumer. Require a fresh, stable tip and positive confirmed pool capacity
before dispatch. Zero confirmed liquidity and positive trusted wallet change
are different states; the operator-health helper reports them separately.

```bash
python3 -m unittest discover -s deploy/fallback/tests -v
```

The tests retain existing cursor, binding and read-only checks. They also check
service and argument restrictions, real oversized child output and redaction.
Verify both operations live through the observer's existing run-as permissions
before relying on them. A bridge update requires no server restart.
