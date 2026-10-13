#!/usr/bin/env python3
"""Evaluate the bounded pool_status bridge reply; run from an existing monitor.

stdin is public bridge JSON. Exit 0 means this snapshot meets the chosen threshold,
2 means operator action is needed, and 1 means unavailable/unqualified evidence.
No wallet, database, daemon or alert delivery service is changed.
"""
import argparse
import json
import math
import sys
import time


def amount(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid integer evidence')
    return value


def evaluate(reply, genesis, server_pubkey, threshold, now, maximum_age=300):
    if (reply.get('kind') != 'pool_status' or type(reply.get('schema_version')) is not int
            or reply.get('schema_version') != 1
            or reply.get('genesis') != genesis or reply.get('server_pubkey') != server_pubkey
            or reply.get('complete') is not True or reply.get('stable_tip') is not True):
        raise ValueError('unqualified pool snapshot')
    observed = reply.get('observed_at')
    if type(observed) not in (int, float) or not math.isfinite(observed):
        raise ValueError('invalid observation time')
    if not -30 <= now - observed <= maximum_age:
        raise ValueError('stale or future observation')
    pool, rounds = reply['pool'], reply['rounds']
    count = amount(pool['eligible_count'])
    total = amount(pool['eligible_amount_sat'])
    confirmed_count = amount(pool['confirmed_eligible_count'])
    confirmed_total = amount(pool['confirmed_eligible_amount_sat'])
    if (count > amount(pool['unspent_count']) or confirmed_count > count
            or confirmed_total > total or bool(count) != bool(total)
            or bool(confirmed_count) != bool(confirmed_total)):
        raise ValueError('inconsistent pool evidence')
    if count:
        expiry = amount(pool['minimum_eligible_expiry_height'])
        if expiry <= amount(reply['height']) + amount(reply['pool_pre_expiry_blocks']):
            raise ValueError('pool coins no longer usable')
    confirmed = amount(rounds['confirmed_balance_sat'])
    trusted = amount(rounds['trusted_balance_sat'])
    if confirmed > trusted:
        raise ValueError('inconsistent rounds balances')
    alerts = []
    if not confirmed_count:
        alerts.append('no_confirmed_usable_pool_coins')
    if confirmed < threshold:
        alerts.append('confirmed_rounds_liquidity_below_reserve')
    if trusted < threshold:
        alerts.append('trusted_rounds_balance_below_reserve')
    return {'status': 'operator_action_required' if alerts else 'healthy_snapshot',
            'observed_at': observed, 'alerts': alerts,
            'confirmed_pool_count': confirmed_count,
            'confirmed_pool_amount_sat': confirmed_total,
            'rounds_confirmed_sat': confirmed, 'rounds_trusted_sat': trusted,
            'low_rounds_balance_alert_sat': threshold,
            'scope': 'bounded current liquidity only; no capital reservation or whole-liability reconciliation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--genesis', required=True)
    parser.add_argument('--server-pubkey', required=True)
    parser.add_argument('--low-balance-sat', required=True, type=int)
    args = parser.parse_args()
    try:
        if args.low_balance_sat <= 0:
            raise ValueError('positive threshold required')
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('snapshot bound exceeded')
        result = evaluate(json.loads(raw), args.genesis, args.server_pubkey,
                          args.low_balance_sat, time.time())
    except (ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'observation_unavailable',
                          'alerts': ['pool_or_liquidity_evidence_unqualified']}))
        return 1
    print(json.dumps(result, allow_nan=False))
    return 2 if result['alerts'] else 0


if __name__ == '__main__':
    sys.exit(main())
