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

## Financial source facts

Two fixed requests take no arguments:

```json
{"kind":"claimant_inventory"}
{"kind":"financial_readiness"}
```

`claimant_inventory` reads twelve fixed source tables in one repeatable-read,
read-only database transaction with an eight-second statement timeout. Each
table has a 2,000-row limit and one extra row detects truncation. The result
contains live physical candidates, pending boards, immutable records and links,
pool rows, known Lightning nodes, payment/receive/HTLC facts and delegated
participation input/output references. The observation includes spent logical
ancestors while their physical backing is unswept. Preimages, invoice secrets,
raw signed forfeits and private keys are never selected. The observation is
bounded by two MiB; exceeding that limit fails the operation rather than
returning a partial inventory as complete.

`candidate_tables_complete` means only that the selected candidate tables fit
the bound in this single database snapshot. `complete` and
`liability_accounting_complete` remain false. Candidate amounts must not be
summed as customer liabilities: delegated predecessors and replacements can
represent one obligation, and internal Lightning rights require arbitration.
Decode policy bytes to distinguish the exact unspendable padding key; neither
330 sats nor `unclaimed` alone identifies padding. Resolve physical backing and
claimant membership independently before counting assets or liabilities.

`financial_readiness` adds the actual Core genesis/tip, before/after tip stability,
both daemon image digests, hashes of their current disk config files and an
allowlist of non-secret fee/timing settings. The whole collection has a
20-second deadline. Disk estimator limits are reported in integer sat/kvB;
they do not prove which configuration a running daemon loaded or which final
transaction rates it enforces. In particular, a replacement/progression path
can add relay fees above an estimator limit.

An optional operator-owned `treasury-wallet-declarations.json`, installed beside
the script, declares public ranged descriptors:

```json
{
  "network": "signet",
  "genesis": "<actual signet genesis hash>",
  "wallets": [
    {
      "member_id": "operator-rounds",
      "wallet_kind": "rounds",
      "identity": "<declared public wallet identity>",
      "descriptors": ["<canonical public ranged descriptor with checksum>"]
    },
    {
      "member_id": "operator-watchman",
      "wallet_kind": "watchman",
      "identity": "<declared public wallet identity>",
      "descriptors": ["<canonical public ranged descriptor with checksum>"]
    }
  ]
}
```

Private descriptors are refused before the local Core call. Core must return
the same canonical descriptor, `hasprivatekeys=false` and `isrange=true`.
These checks establish public descriptor syntax, not daemon ownership,
confirmed fee funds or signer availability. Never commit a live declaration.

The financial reply deliberately reports `qualified=false`, `complete=false`,
null effective path ceilings, null capital and budget availability, and null
pool/channel readiness. `runtime_wallet_inventory` contains two separately timed
owning-daemon source frames when the fixed operation is supported.

Its `gaps` name the missing runtime producers. A healthy container, positive
pool database row or configured fee cap must never be converted into a qualified
dispatch event.
The `snapshot` operation also returns `wallet=null`: the older wallet-status RPC
reveals a new address in memory, so this observer no longer calls it.

The wallet source calls are literal `sudo -n docker exec` arguments in the
existing `fb-captaind` and `fb-watchmand` containers, using the same `captaind rpc
--addr ... wallet-inventory` client. Their existing loopback admin addresses are
`127.0.0.1:3536` and `127.0.0.1:3538`, respectively, as declared by the deployment
templates. No `.env`, private config or keymap is read to discover these calls.
There is no caller-selected container, command, socket or RPC method.

Each source read has a four-second child deadline within the whole 20-second
collection deadline and a two-MiB output limit. The adapter requires the exact
public source contract, at most 2,000 UTXOs and 2,000 reservations, no duplicates,
consistent position/maturity/eligibility flags and a complete local inventory.
Unknown fields, private descriptors, wrong networks and malformed replies are
refused before export. Public descriptors are checked with the fixed local
Core `getdescriptorinfo` operation after private-descriptor refusal. These are
syntax/source checks, not proof of affordable transactions or signer readiness.

Each available frame preserves the original owning response, public descriptor,
synchronized local tip, loaded trust threshold, UTXO values/scripts/derivations,
chain positions, maturity/trust and current reservations. Unsupported old targets,
timeouts, failed reads or invalid contracts produce a named missing source and
`source:null`; they never fall back to `wallet`/`status`, guessed zero or an empty
valid wallet. A stale source tip and duplicate owner descriptors add explicit
gaps. Excessive whole financial output still fails the collection rather than
returning a qualified prefix.

`separate_frames=true`, `globally_atomic=false`, `complete=false` and
`qualified=false` remain explicit in the wallet envelope. Two successful local
wallet reads—even with matching tips—do not establish one atomic treasury epoch,
complete financial membership or permission to dispatch. The committed native
CLI fixtures are unchanged public regtest frames from distinct local proven
runs; replay tests are source-only evidence, not live target qualification.

Still-required fork producers are read-only getters at the owning path:
- A loaded runtime config identity and effective enforced transaction ceilings
  for rounds, pool, quiet sweeps/direct payout, claims, progression and bumps.
  Report an unimplemented limit as unavailable. Include declared ordinary and
  urgent exposure/budget caps and actual remaining consumption; a file on disk
  does not prove these facts.
- Public full-owned channel balances, reserves/in-flight ownership and selected
  route readiness from the actual owning Lightning node. Known node database
  keys alone do not establish positive usable liquidity.
- Resolved logical obligations and complete physical backing membership,
  including delegated and internal-Lightning arbitration and pending carriers.
  Fresh controlled treasury membership must cover every customer/forwarder;
  otherwise mixed backing requires explicit attribution before conservation.

Missing, truncated or stale producers hold dispatch. This bridge does not
introduce an administrative command interface or a second dispatcher.
