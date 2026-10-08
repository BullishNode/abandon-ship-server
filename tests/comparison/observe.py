#!/usr/bin/env python3
"""Record comparison commands and observations without replacing scenario assertions."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def record(root, event):
    root.mkdir(parents=True, exist_ok=True)
    event = {"time": datetime.now(timezone.utc).isoformat(), **event}
    with (root / "observations.jsonl").open("a") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        log.write(json.dumps(event, sort_keys=True) + "\n")
        log.flush()
        os.fsync(log.fileno())
    return event


def index(root):
    events = [json.loads(line) for line in (root / "observations.jsonl").read_text().splitlines()]
    lines = ["# Comparison observations", "", "Generated from observations.jsonl. A start without a finish is incomplete evidence, not a passing run.", ""]
    for e in events:
        detail = e.get("observation", e["kind"]).replace("\n", " ")
        lines.append(f'- {e["time"]} — {e["arm"]} / {e["case"]}: {detail}')
        if "classification" in e:
            lines.append(f'  Class: {e["classification"]}. Expected: {e["expected"]}. Next: {e["next"]}.')
        if "exit_code" in e:
            lines.append(f'  Actual exit {e["exit_code"]}; expected {e["expected_code"]}; matched={e["expectation_met"]}; {e["duration_s"]:.3f}s.')
        for path in e.get("evidence", []):
            lines.append(f"  Evidence: {path}")
    (root / "OBSERVATIONS.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    note = sub.add_parser("note")
    sub.add_parser("index")
    for command in [run, note]:
        command.add_argument("--arm", choices=["A", "B", "C", "D", "common"], required=True)
        command.add_argument("--case", required=True)
    run.add_argument("--cwd", type=Path, default=Path.cwd())
    run.add_argument("--expected-code", type=int, default=0)
    run.add_argument("command", nargs=argparse.REMAINDER)
    note.add_argument("--classification", choices=["reproduced", "source", "inference", "hypothesis", "requirement"], required=True)
    note.add_argument("--observation", required=True)
    note.add_argument("--expected", required=True)
    note.add_argument("--next", required=True)
    note.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "index":
        index(root)
        return 0
    event = {"arm": args.arm, "case": args.case}
    if args.action == "note":
        record(root, {**event, "kind": "observation", "classification": args.classification,
                      "observation": args.observation, "expected": args.expected,
                      "next": args.next, "evidence": args.evidence})
        index(root)
        return 0

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run requires a command after --")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    directory = root / run_id
    directory.mkdir(parents=True, mode=0o700)
    cwd = args.cwd.resolve()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, text=True, capture_output=True)
    diff = subprocess.run(["git", "diff", "HEAD", "--"], cwd=cwd, capture_output=True)
    metadata = {**event, "run_id": run_id, "command": command, "cwd": str(cwd),
                "revision": revision.stdout.strip() if revision.returncode == 0 else None,
                "tracked_diff_sha256": hashlib.sha256(diff.stdout).hexdigest() if diff.returncode == 0 else None,
                "expected_code": args.expected_code,
                "environment": {k: os.environ[k] for k in ["BIN", "COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "RUN"] if k in os.environ}}
    binary = os.environ.get("BIN")
    if binary:
        with open(binary, "rb") as source:
            metadata["binary_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
    (directory / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    evidence = [str(directory / "manifest.json"), str(directory / "output.log")]
    started = time.monotonic()
    record(root, {**metadata, "kind": "run_started", "driver_pid": os.getpid(), "evidence": evidence})
    setup_failed = False
    with (directory / "output.log").open("wb") as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
            code = process.wait()
        except OSError as error:
            output.write(f"Command setup failed: {error}\n".encode())
            code = 127
            setup_failed = True
        output.flush()
        os.fsync(output.fileno())
    record(root, {**event, "kind": "run_finished", "run_id": run_id, "exit_code": code,
                  "expected_code": args.expected_code, "expectation_met": code == args.expected_code,
                  "setup_failed": setup_failed, "duration_s": time.monotonic() - started, "evidence": evidence})
    index(root)
    print(json.dumps({"run_id": run_id, "actual_exit": code, "expected_exit": args.expected_code,
                      "expectation_met": code == args.expected_code, "evidence": str(directory)}))
    # A deliberately expected negative scenario remains an actual failed
    # scenario; its driver decides whether that is the intended regression.
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
