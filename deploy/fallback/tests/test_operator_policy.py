import copy
import importlib.util
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'maintenance' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight, health = load('operator-preflight'), load('operator-health')


class OperatorPolicyTest(unittest.TestCase):
    def setUp(self):
        estimator = {'max_fee_rate': '2sat/vb', 'fallback_fee_rate_fast': '2sat/vb',
                     'fallback_fee_rate_regular': '1sat/vb', 'fallback_fee_rate_slow': '1sat/vb'}
        self.cap = {'network': 'bitcoin', 'fee_estimator': estimator,
                    'vtxo_lifetime': 4320, 'vtxo_exit_delta': 144, 'round_interval': '10m',
                    'vtxopool': {'vtxo_lifetime': 4320},
                    'expiry_payout': {'enabled': True, 'min_payout_sat': 10000}}
        self.watch = {'network': 'bitcoin', 'fee_estimator': copy.deepcopy(estimator)}
        # Synthetic arithmetic fixture, never a production budget/default.
        self.policy = {'network': 'bitcoin', 'fee_budget_sat': 20000,
                       'planned_funding_vbytes': 10000, 'operator_fee_reserve_sat': 20000,
                       'rounds_working_capital_sat': 100000, 'pool_refill_sat': 80000,
                       'payout_and_bump_reserve_sat': 10000,
                       'opening_confirmed_rounds_sat': 210000,
                       'wallet_exit_margin_blocks': 12, 'wallet_refresh_threshold_blocks': 144,
                       'lifetime_slack_blocks': 12}
        self.reply = {'kind': 'pool_status', 'schema_version': 1, 'genesis': 'g', 'server_pubkey': 's',
                      'observed_at': 1000, 'stable_tip': True, 'complete': True,
                      'height': 100, 'pool_pre_expiry_blocks': 10,
                      'pool': {'unspent_count': 8, 'eligible_count': 8, 'eligible_amount_sat': 80000,
                               'confirmed_eligible_count': 8, 'confirmed_eligible_amount_sat': 80000,
                               'minimum_eligible_expiry_height': 148},
                      'rounds': {'trusted_balance_sat': 120000, 'confirmed_balance_sat': 110000}}

    def test_budget_ceiling_and_reserves(self):
        result = preflight.check(self.cap, self.watch, self.policy)
        self.assertEqual(210000, result['required_confirmed_capital_sat'])
        self.assertEqual(110000, result['low_rounds_balance_alert_sat'])
        self.policy['fee_budget_sat'] -= 1
        with self.assertRaises(ValueError):
            preflight.check(self.cap, self.watch, self.policy)

    def test_rejects_uncapped_daemon_and_unclamped_startup_fallback(self):
        for changed in [self.cap, self.watch]:
            original = copy.deepcopy(changed['fee_estimator'])
            for field, value in [('max_fee_rate', None), ('fallback_fee_rate_fast', '3sat/vb')]:
                changed['fee_estimator'][field] = value
                with self.subTest(config=changed, field=field), self.assertRaises(ValueError):
                    preflight.check(self.cap, self.watch, self.policy)
                changed['fee_estimator'] = copy.deepcopy(original)

    def test_mainnet_does_not_inherit_signet_parameters(self):
        for field, value in [('vtxo_lifetime', 48), ('round_interval', '30s')]:
            changed = copy.deepcopy(self.cap)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                preflight.check(changed, self.watch, self.policy)
        self.cap['expiry_payout']['min_payout_sat'] = 1000
        with self.assertRaises(ValueError):
            preflight.check(self.cap, self.watch, self.policy)

    def test_safety_rule_applies_even_with_mainnet_lifetime_floor(self):
        self.policy['wallet_refresh_threshold_blocks'] = 4300
        with self.assertRaises(ValueError):
            preflight.check(self.cap, self.watch, self.policy)

    def test_real_render_keeps_signet_timings_and_caps_both_daemons(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deploy = root / 'deploy'
            shutil.copytree(source / 'templates', deploy / 'fallback/templates')
            for name in ['render.sh', 'signet-fallback.env']:
                shutil.copyfile(source / name, deploy / 'fallback' / name)
            state = deploy / 'state/signet-fallback'
            state.mkdir(parents=True)
            (state / 'sweep.env').write_text('SWEEP_ADDRESS=fixture-public-address\n')
            (state / 'secrets.env').write_text('PG_PASS=fixture-not-a-credential\n')
            original = root / 'original/state/signet'
            original.mkdir(parents=True)
            (original / 'secrets.env').write_text('RPC_USER=fixture\nRPC_PASS=fixture-not-a-credential\n')
            subprocess.run(['sh', str(deploy / 'fallback/render.sh'), 'enabled'],
                           cwd=deploy, env={**os.environ, 'SIGNET_DEPLOY': str(root / 'original')},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            cap = tomllib.loads((state / 'captaind.toml').read_text())
            watch = tomllib.loads((state / 'watchmand.toml').read_text())
            for config in [cap, watch]:
                self.assertEqual('20sat/vb', config['fee_estimator']['max_fee_rate'])
            self.assertEqual(48, cap['vtxo_lifetime'])
            self.assertEqual(48, cap['vtxopool']['vtxo_lifetime'])
            self.assertEqual('30s', cap['round_interval'])
            self.assertEqual(1000, cap['expiry_payout']['min_payout_sat'])
            policy = {**self.policy, 'network': 'signet', 'fee_budget_sat': 40000,
                      'planned_funding_vbytes': 2000, 'operator_fee_reserve_sat': 50000,
                      'rounds_working_capital_sat': 160000, 'opening_confirmed_rounds_sat': 300000,
                      'wallet_refresh_threshold_blocks': 12}
            result = preflight.check(cap, watch, policy)
            self.assertEqual(20, result['estimator_max_sat_vb'])
            self.assertEqual(300000, result['required_confirmed_capital_sat'])
            self.assertEqual(140000, result['low_rounds_balance_alert_sat'])
            policy['planned_funding_vbytes'] += 1
            with self.assertRaises(ValueError):
                preflight.check(cap, watch, policy)

    def test_missing_reserve_is_not_operating_capital(self):
        self.policy['opening_confirmed_rounds_sat'] -= 1
        with self.assertRaises(ValueError):
            preflight.check(self.cap, self.watch, self.policy)

    def test_zero_confirmed_funds_is_liquidity_alert_not_insolvency(self):
        self.reply['rounds']['confirmed_balance_sat'] = 0
        result = health.evaluate(self.reply, 'g', 's', 100000, 1001)
        self.assertEqual(['confirmed_rounds_liquidity_below_reserve'], result['alerts'])
        self.assertEqual(120000, result['rounds_trusted_sat'])

    def test_pool_empty_raises_action_even_if_rounds_funded(self):
        self.reply['pool'].update(eligible_count=0, eligible_amount_sat=0,
                                 confirmed_eligible_count=0, confirmed_eligible_amount_sat=0,
                                 minimum_eligible_expiry_height=None)
        result = health.evaluate(self.reply, 'g', 's', 100000, 1001)
        self.assertEqual(['no_confirmed_usable_pool_coins'], result['alerts'])

    def test_stale_wrong_identity_incomplete_or_expiring_pool_is_unqualified(self):
        variants = [('observed_at', 600), ('genesis', 'wrong'), ('stable_tip', False), ('complete', False)]
        for field, value in variants:
            changed = copy.deepcopy(self.reply)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                health.evaluate(changed, 'g', 's', 100000, 1001)
        self.reply['pool']['minimum_eligible_expiry_height'] = 110
        with self.assertRaises(ValueError):
            health.evaluate(self.reply, 'g', 's', 100000, 1001)


if __name__ == '__main__':
    unittest.main()
