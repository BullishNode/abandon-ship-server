import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('wallet_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
FIXTURES=Path(__file__).with_name('fixtures')/'wallet-inventory'


def native(owner):return json.loads((FIXTURES/('native-'+owner+'.json')).read_text())


def checked_descriptor(*args):
    if args[:2]!=('cli','getdescriptorinfo'):raise AssertionError('unexpected operation')
    return json.dumps({'hasprivatekeys':False,'isrange':True,'descriptor':args[2]})


class OwningWalletSourceTest(unittest.TestCase):
    def test_actual_native_frames_preserved_without_global_atomic_claim(self):
        sources={owner:native(owner) for owner in ('rounds','watchman')}
        def fixed_read(argv,timeout):
            owner='rounds' if argv[4].endswith('fb-captaind-1') else 'watchman'
            self.assertEqual(argv,['sudo','-n','docker','exec',bridge.WALLET_SOURCES[owner][1],
                'captaind','rpc','--addr',bridge.WALLET_SOURCES[owner][2],'wallet-inventory'])
            self.assertEqual(timeout,4)
            return json.dumps(sources[owner])
        with patch.object(bridge,'run',side_effect=fixed_read) as operation,patch.object(bridge,'ops',side_effect=checked_descriptor):
            result=bridge.runtime_wallet_inventory('regtest')
        self.assertEqual(operation.call_count,2)
        self.assertTrue(result['owning_sources_available'])
        self.assertTrue(result['separate_frames']);self.assertFalse(result['globally_atomic'])
        self.assertFalse(result['complete']);self.assertFalse(result['qualified'])
        for owner in sources:
            frame=result['frames'][owner]
            self.assertEqual(frame['source'],sources[owner])
            self.assertTrue(frame['descriptor_public_canonical_checked'])
            self.assertLessEqual(frame['observed_at'],frame['completed_at'])
        self.assertNotEqual(sources['rounds']['rounds']['tip_hash'],sources['watchman']['watchman']['tip_hash'])

    def test_old_unsupported_or_timed_out_target_is_missing_not_zero_or_status(self):
        for output in ({'rounds':{'address':'legacy-mutating-status','total_balance':0}}, {}, []):
            with self.subTest(output=output),patch.object(bridge,'run',return_value=json.dumps(output)) as read,patch.object(bridge,'ops') as core:
                result=bridge.owning_wallet_inventory('rounds','signet')
            self.assertFalse(result['available']);self.assertIsNone(result['source'])
            self.assertIn('unavailable_unsupported_or_invalid',result['missing_source'])
            self.assertEqual(read.call_count,1);self.assertEqual(read.call_args.args[0][-1],'wallet-inventory')
            core.assert_not_called()
        for error in (subprocess.CalledProcessError(2,['fixed-wallet-inventory']),ValueError('deadline exceeded'),OSError('unavailable')):
            with self.subTest(error=type(error).__name__),patch.object(bridge,'run',side_effect=error),patch.object(bridge,'ops') as core:
                result=bridge.owning_wallet_inventory('watchman','signet')
            self.assertFalse(result['available']);self.assertIsNone(result['source']);core.assert_not_called()

    def test_private_unknown_or_mismatched_fields_refuse_before_core(self):
        samples=[]
        for scope in ('top','wallet','row'):
            value=native('rounds');target=value if scope=='top' else value['rounds'] if scope=='wallet' else value['rounds']['utxos'][0]
            target['private_key']='synthetic-test-secret';samples.append(value)
        value=native('rounds');value['rounds']['public_descriptor']='tr(tprv'+'A'*100+'/0/*)#abcdefgh';samples.append(value)
        value=native('rounds');value['missing_sources']=['synthetic-test-secret'];samples.append(value)
        value=native('rounds');value['complete']=True;samples.append(value)
        value=native('rounds');value['rounds']['complete']=False;samples.append(value)
        value=native('rounds');value['rounds']['network']='signet';samples.append(value)
        for value in samples:
            with self.subTest(keys=list(value)),patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops') as core:
                result=bridge.owning_wallet_inventory('rounds','regtest')
            self.assertFalse(result['available']);self.assertIsNone(result['source']);core.assert_not_called()
            self.assertNotIn('test-secret',json.dumps(result));self.assertNotIn('tprv',json.dumps(result))

    def test_row_and_byte_bounds_refuse_entire_source(self):
        value=native('rounds');base=value['rounds']['utxos'][0]
        value['rounds']['utxos']=[dict(base,outpoint=f'{i:064x}:0') for i in range(2000)]
        self.assertEqual(2000,len(bridge.validate_wallet_frame(value,'rounds','regtest')['rounds']['utxos']))
        value['rounds']['utxos'].append(dict(base,outpoint=f'{2000:064x}:0'))
        with patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops') as core:
            result=bridge.owning_wallet_inventory('rounds','regtest')
        self.assertFalse(result['available']);self.assertIsNone(result['source']);core.assert_not_called()
        for owner in ('rounds','watchman'):
            source=json.dumps(native(owner))
            with patch.object(bridge,'MAX_BYTES',len(source.encode())),patch.object(bridge,'run',return_value=source),patch.object(bridge,'ops',side_effect=checked_descriptor):
                self.assertTrue(bridge.owning_wallet_inventory(owner,'regtest')['available'])
            with patch.object(bridge,'MAX_BYTES',len(source.encode())-1),patch.object(bridge,'run',return_value=source),patch.object(bridge,'ops') as core:
                result=bridge.owning_wallet_inventory(owner,'regtest')
            self.assertFalse(result['available']);self.assertIsNone(result['source']);core.assert_not_called()

    def test_duplicates_or_inconsistent_flags_do_not_pass_validation(self):
        samples=[]
        value=native('watchman');value['watchman']['utxos']*=2;samples.append(value)
        value=native('watchman');value['watchman']['reserved_outpoints']=[value['watchman']['utxos'][0]['outpoint']]*2;samples.append(value)
        for field,replacement in [('reserved',True),('mature',False),('confirmations',4),('value_sat',True),('trusted',1),('script_pubkey','')]:
            value=native('watchman');value['watchman']['utxos'][0][field]=replacement;samples.append(value)
        value=native('watchman');value['watchman']['utxos'][0]['coinbase']=True;samples.append(value)
        for value in samples:
            with self.subTest(row=value['watchman']['utxos'][0]),self.assertRaises(ValueError):
                bridge.validate_wallet_frame(value,'watchman','regtest')

    def test_locked_and_unconfirmed_facts_remain_original_source_flags(self):
        value=native('watchman');wallet=value['watchman'];row=wallet['utxos'][0]
        wallet['reserved_outpoints']=[row['outpoint']];row.update(reserved=True,transaction_eligible=False,confirmed_fee_eligible=False)
        self.assertIs(bridge.validate_wallet_frame(value,'watchman','regtest'),value)
        row.update(reserved=False,confirmed_height_upper_bound=None,confirmation_block_hash=None,confirmation_time=None,
            unconfirmed_first_seen=1,unconfirmed_last_seen=2,confirmations=0,transaction_eligible=True)
        wallet['reserved_outpoints']=[]
        self.assertIs(bridge.validate_wallet_frame(value,'watchman','regtest'),value)
        self.assertFalse(row['confirmed_fee_eligible'])

    def test_bad_core_descriptor_info_is_missing_without_secret_echo(self):
        for details in ({'hasprivatekeys':True}, {'hasprivatekeys':False,'isrange':False}, [], {}):
            with self.subTest(details=details),patch.object(bridge,'run',return_value=json.dumps(native('rounds'))),patch.object(bridge,'ops',return_value=json.dumps(details)):
                result=bridge.owning_wallet_inventory('rounds','regtest')
            self.assertFalse(result['available']);self.assertIsNone(result['source'])

    def test_fixed_wallet_deadline_and_smaller_global_deadline_stop_real_child(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(bridge,'DEPLOY',Path(folder)):
            with self.assertRaisesRegex(ValueError,'deadline'):
                bridge.run([sys.executable,'-c','import time; time.sleep(1)'],timeout=0.02)
            with patch.object(bridge,'READ_DEADLINE',bridge.time.monotonic()+0.02),self.assertRaisesRegex(ValueError,'deadline'):
                bridge.run([sys.executable,'-c','import time; time.sleep(1)'],timeout=4)

    def test_financial_collection_keeps_null_caps_budgets_exposure_and_rights_gaps(self):
        # These modified-network signet unit frames test collection logic only.
        # Actual native fixtures above remain unchanged regtest source evidence.
        frames={owner:native(owner) for owner in ('rounds','watchman')}
        for owner in frames:frames[owner][owner]['network']='signet'
        chain={'chain':'signet','bestblockhash':'a'*64,'blocks':1000,'initialblockdownload':False}
        def operation(*args):
            if args[1]=='getblockchaininfo':return json.dumps(chain)
            if args[1]=='getblockhash':return 'b'*64
            return checked_descriptor(*args)
        def process(argv,**kwargs):
            if argv[3]=='inspect':return json.dumps({'status':'running'})
            owner='rounds' if argv[4].endswith('fb-captaind-1') else 'watchman'
            return json.dumps(frames[owner])
        with patch.object(bridge,'ops',side_effect=operation),patch.object(bridge,'run',side_effect=process),patch.object(bridge,'disk_financial_config',return_value={}),patch.object(bridge,'public_wallet_declarations',return_value={'wallets_declared_complete':True}),patch.object(bridge,'claimant_inventory',return_value={'truncated_tables':[]}):
            result=bridge.financial_readiness()
        self.assertTrue(result['runtime_wallet_inventory']['owning_sources_available'])
        self.assertFalse(result['complete']);self.assertFalse(result['qualified'])
        self.assertFalse(result['runtime_wallet_inventory']['globally_atomic'])
        for key in ('effective_fee_ceilings_sat_kvb','available_fee_funding_msat','capital_exposure_msat','budget_remaining_msat','pool_ready','channel_route_ready'):
            self.assertIsNone(result[key])
        for gap in ('resolved_claimant_and_physical_backing_membership','loaded_runtime_configuration_identity_and_enforced_path_caps',
            'epoch_budget_and_inflight_capital_consumption','lightning_full_owned_channel_balances_and_selected_route_readiness',
            'rounds_wallet_source_tip_not_current','watchman_wallet_source_tip_not_current'):
            self.assertIn(gap,result['gaps'])
        self.assertNotIn('runtime_wallet_inventory_with_canonical_positions_and_active_input_locks',result['gaps'])
        self.assertIsNone(bridge.READ_DEADLINE)


if __name__=='__main__':unittest.main()
