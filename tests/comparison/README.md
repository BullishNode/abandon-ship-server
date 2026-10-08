# Comparison evidence

`observe.py` records a command's immutable Git revision, tracked-diff hash, selected non-secret environment, optional `BIN` hash, stdout/stderr, exit status and elapsed time. `observations.jsonl` is append-only; `OBSERVATIONS.md` is its readable index. Each run has a new artifact directory. An expected negative scenario still records and returns its actual nonzero exit; the comparison driver must check the intended failing assertion.

```sh
python3 tests/comparison/observe.py --root /path/to/private/evidence run \
  --arm A --case restore-gate --cwd "$PWD" -- bash tests/regtest/restore-gate.sh
python3 tests/comparison/observe.py --root /path/to/private/evidence note \
  --arm common --case example --classification source \
  --expected 'Describe the required behavior' \
  --observation 'Describe the observed fact, not a readiness claim' \
  --next 'Describe remaining verification' --evidence /path/to/source-or-log
```

Keep secrets out of command arguments and notes. Raw logs can contain private wallet data; keep the evidence directory private and publish only reviewed summaries. The diff hash does not capture untracked files or replace a source snapshot: freeze the tested source/binary and record that artifact before qualification. A missing finish event is incomplete evidence; inspect the recorded process before deciding whether it stopped.

## Settlement oracle

`oracle.py` checks effective settlements using declared workload lineage and decoded Bitcoin Core transactions. It detects separate payments of one entitlement and payment of both an ancestor and its replacement. Split outputs can settle independently. Shared-key coins share one output; their total deduction must equal the actual input-minus-output mining fee and satisfy the percentage cap.

The evidence JSON contains:

- `coins`: IDs mapped to `parents` (prior coin IDs), `amount_sat` and `payout_script` (hex).
- `payouts`: entries with `txid` and selected `coins`; `exits`: entries with `txid` and `coin`.
- `max_fee_pct`, and `transactions` keyed by txid, using Core's verbose transaction format.
- Optional `sweeps`: `{coin, txid}` proofs, with `sweep_scripts` and `sweep_min_confs`. When present, every paid coin must have a confirmed sweep spending an exact outpoint in its workload `path`, paying only allowed scripts apart from OP_RETURN/P2A. `predecessor_sweep_ids` lists original inputs that also require proof for an unclaimed replacement. Missing required proofs fail.

`--bitcoin-cli /path/to/wrapper` fetches settlement transactions and their funding transactions through the selected arm's Core. `--save evidence.json` retains the captured facts before verification, including a counterexample. The wrapper obtains credentials from its local configuration. To verify saved evidence offline:

```sh
python3 tests/comparison/oracle.py evidence.json
python3 -m unittest discover -s tests/comparison -p test_oracle.py -v
```

`cargo run --locked --example vtxo_facts` reads one encoded VTXO as hex on stdin and returns its public ID, amount, BIP86 script and exit-path outpoints. Build the manifest from workload/client facts, independently of the sidecar's payment decision. Record which ancestry is known: undeclared ancestry cannot be checked. The helper decodes the pinned Ark format; it does not validate the path against the chain.

The oracle is used in addition to live scenario assertions. The separate capital check below covers a declared transaction graph. Complete capital cycles, pending-claim recoverability, seeded flow generation and arm adapters remain unfinished. Those parts of the requested common framework remain open. Sensitivity tests use synthetic transaction facts; actual before/after evidence is distinguished in `../results/2026-10-07-comparison-oracle.txt`.

Path facts must come from recorded client/VTXO data; this oracle does not itself verify Ark signatures or rederive transaction chains. The optional path proof is not enabled implicitly in older manifests. Actual sweep capture and a wrong-outpoint sensitivity check are recorded in `../results/2026-10-07-comparison-paths.txt`. Arm labels A/B/C/D do not imply an adapter or implementation is complete.

## Capital check

`capital.py evidence.json` reconciles a declared transaction graph with Core's observed unspent outputs. `opening` and optional `external_inputs` map outpoints to `{amount_sat, bucket}`; their amounts are checked against the funding transactions. `steps` lists transaction IDs in dependency order; `transactions` contains their verbose Core data and the funding transactions. Every additional input must be declared, so an unnoticed faucet top-up fails. Repeated inputs/transactions fail.

`output_buckets` assigns each resulting outpoint to `rounds`, `watchman`, `payout`, `tree`, `user` or `external`. Record this ownership from the workload and destination scripts. `unspent` must independently capture the closing outpoints and amounts using Core's `gettxout`, with mempool spending enabled. A subsequent transaction omitted from the graph makes the check fail. Optional `closing_min_sat` checks a bucket's capital floor, so funds stranded in the payout wallet cannot satisfy a rounds-wallet requirement.

The check counts internal hops once and reports opening value, declared external inflow, remaining value by owner and every mining fee. It does not infer ownership, discover an omitted independent transaction graph, or measure liabilities. It cannot by itself establish a ten-cycle liquidity pass. Core capture must happen at a stable checkpoint; preserve the block height and do not spend from the tested graph during capture. Nonzero OP_RETURN burns are explicitly unsupported.
