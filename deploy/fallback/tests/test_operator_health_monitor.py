import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

PATH = Path(__file__).parents[1] / 'maintenance' / 'operator-health-monitor.py'
spec = importlib.util.spec_from_file_location('health_monitor', PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def policy(self):
        return {'genesis': 'genesis', 'server_pubkey': 'server', 'low_balance_sat': 140000}

    def reply(self, count=8):
        return {'kind': 'pool_status', 'schema_version': 1, 'genesis': 'genesis',
                'server_pubkey': 'server', 'complete': True, 'stable_tip': True,
                'observed_at': 1000, 'height': 100, 'pool_pre_expiry_blocks': 10,
                'pool': {'unspent_count': 8, 'eligible_count': 8, 'eligible_amount_sat': 80000,
                         'confirmed_eligible_count': count, 'confirmed_eligible_amount_sat': count * 10000,
                         'minimum_eligible_expiry_height': 148},
                'rounds': {'confirmed_balance_sat': 300000, 'trusted_balance_sat': 300000}}

    def run_sample(self, reply):
        output = subprocess.CompletedProcess([], 0, json.dumps(reply).encode(), b'')
        with patch.object(monitor.subprocess, 'run', return_value=output) as run, patch.object(monitor.time, 'time', return_value=1001):
            result = monitor.sample(self.policy())
            self.assertEqual(b'{"kind":"pool_status"}', run.call_args.kwargs['input'])
            self.assertEqual(15, run.call_args.kwargs['timeout'])
            return result

    def test_positive_capacity_is_healthy(self):
        result, code = self.run_sample(self.reply())
        self.assertEqual(0, code)
        self.assertEqual([], result['alerts'])

    def test_funded_bank_does_not_hide_unconfirmed_pool(self):
        result, code = self.run_sample(self.reply(count=0))
        self.assertEqual(2, code)
        self.assertEqual(['no_confirmed_usable_pool_coins'], result['alerts'])

    def test_wrong_identity_is_unqualified(self):
        reply = self.reply()
        reply['server_pubkey'] = 'wrong-server'
        result, code = self.run_sample(reply)
        self.assertEqual(1, code)
        self.assertEqual(['pool_or_liquidity_evidence_unqualified'], result['alerts'])

    def test_bridge_timeout_is_visible(self):
        with patch.object(monitor.subprocess, 'run', side_effect=subprocess.TimeoutExpired('bridge', 15)):
            result, code = monitor.sample(self.policy())
        self.assertEqual(1, code)
        self.assertEqual(['pool_or_liquidity_evidence_unqualified'], result['alerts'])

    def test_low_float_alerts_are_separate(self):
        reply = self.reply()
        reply['rounds'] = {'confirmed_balance_sat': 100000, 'trusted_balance_sat': 130000}
        result, code = self.run_sample(reply)
        self.assertEqual(2, code)
        self.assertEqual(['confirmed_rounds_liquidity_below_reserve',
                          'trusted_rounds_balance_below_reserve'], result['alerts'])
