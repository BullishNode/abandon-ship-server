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

Keep secrets out of command arguments and notes. Raw logs can contain private wallet data; keep the evidence directory private and publish only reviewed summaries. The diff hash does not capture untracked files or replace a source snapshot: freeze the tested source/binary and record that artifact before qualification. A missing finish event is incomplete evidence; inspect the recorded process before deciding whether it stopped. This recorder is not a flow simulator or settlement oracle; those comparison components are still being built.
