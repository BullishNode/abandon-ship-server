#!/usr/bin/env python3
"""Sample the owned read-only bridge and emit a bounded operator alert."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time


def sample(policy):
    spec = importlib.util.spec_from_file_location('operator_health', Path(__file__).with_name('operator-health.py'))
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    try:
        threshold = policy['low_balance_sat']
        if type(threshold) is not int or threshold <= 0:
            raise ValueError('positive reserve threshold required')
        reply = subprocess.run(
            ['/usr/bin/python3', '/opt/abandon-simulation/server-observer.py'],
            input=b'{"kind":"pool_status"}', capture_output=True, timeout=15, check=True,
        )
        if len(reply.stdout) > 65536:
            raise ValueError('bridge response bound exceeded')
        result = health.evaluate(json.loads(reply.stdout), policy['genesis'],
                                policy['server_pubkey'], threshold, time.time())
        return result, 2 if result['alerts'] else 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return {'status': 'observation_unavailable',
                'alerts': ['pool_or_liquidity_evidence_unqualified']}, 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, type=Path)
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text())
    except (OSError, ValueError):
        print(json.dumps({'status': 'observation_unavailable', 'alerts': ['operator_policy_unavailable']}))
        return 1
    result, code = sample(policy)
    print(json.dumps(result, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
