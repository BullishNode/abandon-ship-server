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

## Validation

```bash
python3 -m unittest discover -s deploy/fallback/tests -v
```

The tests retain existing cursor, binding and read-only checks. They also check
service and argument restrictions, real oversized child output and redaction.
Verify both operations live through the observer's existing run-as permissions
before relying on them. A bridge update requires no server restart.
