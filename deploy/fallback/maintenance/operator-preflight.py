#!/usr/bin/env python3
"""Read rendered daemon TOML and an operator-approved JSON capital policy.

No wallet, daemon, database or configuration is changed. Success checks declared
configuration and arithmetic, not actual capital, demand or production readiness.
"""
import argparse
import json
import re
import sys
import tomllib
from decimal import Decimal
from pathlib import Path


def integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def rate(value):
    # Restrict policy input to the native config's integer sat/vb representation.
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*sat/vb', value):
        raise ValueError('fee policy must use positive integer sat/vb rates')
    return int(value[:-6])


def duration(value):
    match = re.fullmatch(r'([1-9][0-9]*)(s|m|min|h)', value or '')
    if not match:
        raise ValueError('round_interval must use positive s, m, min or h duration')
    return int(match[1]) * {'s': 1, 'm': 60, 'min': 60, 'h': 3600}[match[2]]


def check(captaind, watchmand, policy):
    network = captaind.get('network')
    if network not in {'bitcoin', 'signet'} or watchmand.get('network') != network:
        raise ValueError('daemon networks must agree and be bitcoin or signet')
    if policy.get('network') != network:
        raise ValueError('capital policy network does not match daemon configuration')
    caps = []
    for name, config in [('captaind', captaind), ('watchmand', watchmand)]:
        estimator = config.get('fee_estimator', {})
        ceiling = rate(estimator.get('max_fee_rate'))
        for target in ['fast', 'regular', 'slow']:
            if rate(estimator.get('fallback_fee_rate_' + target)) > ceiling:
                raise ValueError(f'{name} fallback {target} exceeds its ceiling')
        caps.append(ceiling)
    if caps[0] != caps[1]:
        raise ValueError('captaind and watchmand must use the same declared ceiling')
    budget = integer(policy.get('fee_budget_sat'), 'fee_budget_sat')
    volume = integer(policy.get('planned_funding_vbytes'), 'planned_funding_vbytes')
    if caps[0] * volume > budget:
        raise ValueError('configured ceiling exceeds the declared funding fee budget')
    fee_reserve = integer(policy.get('operator_fee_reserve_sat'), 'operator_fee_reserve_sat')
    if fee_reserve < budget:
        raise ValueError('operator fee reserve does not cover the declared fee budget')
    working = integer(policy.get('rounds_working_capital_sat'), 'rounds_working_capital_sat')
    pool = integer(policy.get('pool_refill_sat'), 'pool_refill_sat', 0)
    payouts = integer(policy.get('payout_and_bump_reserve_sat'), 'payout_and_bump_reserve_sat')
    if captaind.get('cln_array') and not pool:
        raise ValueError('Lightning enabled without declared pool refill capital')
    required = working + pool + fee_reserve + payouts
    opening = integer(policy.get('opening_confirmed_rounds_sat'), 'opening_confirmed_rounds_sat')
    if opening < required:
        raise ValueError('declared opening confirmed capital is below all reserves combined')
    lifetime = integer(captaind.get('vtxo_lifetime'), 'vtxo_lifetime')
    pool_lifetime = integer(captaind.get('vtxopool', {}).get('vtxo_lifetime'), 'pool lifetime')
    margin = integer(policy.get('wallet_exit_margin_blocks'), 'wallet_exit_margin_blocks')
    refresh = integer(policy.get('wallet_refresh_threshold_blocks'), 'wallet_refresh_threshold_blocks')
    slack = integer(policy.get('lifetime_slack_blocks'), 'lifetime_slack_blocks')
    delta = integer(captaind.get('vtxo_exit_delta'), 'vtxo_exit_delta')
    minimum_lifetime = 2 * margin + delta + refresh + slack
    payout_min = captaind.get('expiry_payout', {}).get('min_payout_sat')
    if network == 'bitcoin':
        if min(lifetime, pool_lifetime) < max(4320, minimum_lifetime):
            raise ValueError('mainnet lifetime violates minimum or wallet safety/slack rule')
        if payout_min != 10000:
            raise ValueError('mainnet must explicitly set min_payout_sat = 10000 net')
        if not 600 <= duration(captaind.get('round_interval')) <= 900:
            raise ValueError('mainnet rounds must batch at 10 to 15 minute intervals')
    elif lifetime != 48 or pool_lifetime != 48 or payout_min != 1000:
        raise ValueError('this signet rehearsal must retain lifetime 48 and minimum 1000')
    if not captaind.get('expiry_payout', {}).get('enabled'):
        raise ValueError('automatic expiry payouts are not enabled')
    return {'status': 'configuration_pass', 'network': network,
            'estimator_max_sat_vb': caps[0], 'funding_fee_budget_sat': budget,
            'planned_funding_vbytes': volume,
            'required_confirmed_capital_sat': required,
            'low_rounds_balance_alert_sat': pool + fee_reserve + payouts,
            'minimum_lifetime_blocks': minimum_lifetime,
            'scope': 'declared policy and rendered config only; verify live confirmed capital separately',
            'limits': ['estimator cap is not an absolute sweep replacement/package fee cap',
                       'payout direct Core estimate and CPFP costs require separate budgets',
                       'an alert does not reserve money or prevent subsequent wallet spending']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captaind', required=True, type=Path)
    parser.add_argument('--watchmand', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = check(tomllib.loads(args.captaind.read_text()),
                       tomllib.loads(args.watchmand.read_text()),
                       json.loads(args.policy.read_text(), parse_float=Decimal))
    except (ValueError, OSError, TypeError, KeyError):
        # Never echo daemon TOML or a malformed private policy file.
        print('operator configuration preflight refused', file=sys.stderr)
        return 1
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
