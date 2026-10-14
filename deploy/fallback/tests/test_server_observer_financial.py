import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('financial_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
TABLES=tuple(sorted(bridge.FINANCIAL_TABLES))
GENESIS='a'*64
TIP='b'*64


def empty_candidates():return {name:[] for name in TABLES}


class FinancialBridgeTest(unittest.TestCase):
    def test_operations_have_no_client_selected_query_path_or_command(self):
        for kind in ('financial_readiness','claimant_inventory'):
            self.assertEqual((kind,None),bridge.request(json.dumps({'kind':kind}).encode()))
            for field,value in [('sql','SELECT 1'),('after','coin'),('wallet','rounds'),('path','/data/seed')]:
                with self.subTest(kind=kind,field=field),self.assertRaises(ValueError):
                    bridge.request(json.dumps({'kind':kind,field:value}).encode())

    def test_raw_candidates_are_never_resolved_customer_liabilities(self):
        source=empty_candidates()
        source['coins']=[{'vtxo_id':'old','amount':2000,'spend_state':'spent'},
                         {'vtxo_id':'new','amount':2000,'spend_state':'unclaimed'}]
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n'+json.dumps(source)+'\nCOMMIT\n') as operation:
            result=bridge.claimant_inventory()
        sql=operation.call_args.args[1]
        self.assertIn('REPEATABLE READ READ ONLY',sql)
        self.assertIn("statement_timeout='8s'",sql)
        self.assertNotIn("spend_state='spendable'",sql)
        self.assertIn('onchain_spent_txid IS NULL',sql)
        self.assertTrue(result['candidate_tables_complete'])
        self.assertFalse(result['complete']);self.assertFalse(result['liability_accounting_complete'])
        self.assertEqual(2,len(result['rows']['coins']))

    def test_truncation_refuses_completeness_for_every_table(self):
        for table in TABLES:
            source=empty_candidates();source[table]=[{'id':i} for i in range(2001)]
            with self.subTest(table=table),patch.object(bridge,'ops',return_value=json.dumps(source)):
                result=bridge.claimant_inventory()
                self.assertFalse(result['candidate_tables_complete'])
                self.assertEqual([table],result['truncated_tables'])
                self.assertEqual(2000,len(result['rows'][table]))

    def test_old_schema_or_unrecognized_inventory_is_not_zero_liability(self):
        for source in ('{}','[]',json.dumps({'coins':[]})):
            with self.subTest(source=source),patch.object(bridge,'ops',return_value=source),self.assertRaises(ValueError):
                bridge.claimant_inventory()
        with patch.object(bridge,'ops',side_effect=subprocess.CalledProcessError(1,['psql'])),self.assertRaises(subprocess.CalledProcessError):
            bridge.claimant_inventory()

    def test_config_exports_allowlist_and_hash_but_never_claims_path_enforcement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state/signet-fallback';state.mkdir(parents=True)
            (state/'watchmand.toml').write_text('network="signet"\n[fee_estimator]\nmax_fee_rate="2sat/vb"\nfallback_fee_rate_regular="1sat/vb"\n[watchman]\nincremental_relay_fee="100 sat/kvb"\n[bitcoind]\nrpc_pass="secret-value"\n')
            with patch.object(bridge,'DEPLOY',root):result=bridge.disk_financial_config('watchmand')
        self.assertNotIn('secret-value',json.dumps(result));self.assertNotIn('rpc_pass',json.dumps(result))
        self.assertEqual(2000,result['estimator_max_fee_rate_sat_kvb'])
        self.assertEqual(100,result['watchman_incremental_relay_fee_sat_kvb'])
        self.assertFalse(result['runtime_loaded_identity_verified'])

    def test_fee_units_are_exact_and_unsupported_values_refused(self):
        self.assertEqual(1234,bridge.fee_rate_sat_kvb('1.234sat/vb'))
        self.assertEqual(1234,bridge.fee_rate_sat_kvb('1234 sat/kvb'))
        for value in (2,True,'-1sat/vb','NaNsat/vb','2btc/kb','0sat/vb','0.0001sat/vb'):
            with self.subTest(value=value),self.assertRaises(ValueError):bridge.fee_rate_sat_kvb(value)

    def test_public_descriptors_checked_by_fixed_core_operation(self):
        descriptor='tr(tpub'+'A'*100+'/0/*)#abcdefgh'
        declaration={'network':'signet','genesis':GENESIS,'wallets':[
            {'member_id':'op-rounds','wallet_kind':'rounds','identity':'rounds-wallet','descriptors':[descriptor]}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'wallets.json';path.write_text(json.dumps(declaration))
            with patch.object(bridge,'WALLET_DECLARATIONS',path),patch.object(bridge,'ops',return_value=json.dumps({'hasprivatekeys':False,'isrange':True,'descriptor':descriptor})) as operation:
                result=bridge.public_wallet_declarations(GENESIS)
        self.assertEqual(('cli','getdescriptorinfo',descriptor),operation.call_args.args)
        self.assertTrue(result['verified']);self.assertIn('daemon ownership',result['validation_scope'])

    def test_private_or_wrong_chain_descriptor_never_reaches_rpc(self):
        for descriptor,genesis in [('tr(tprv'+'A'*100+'/0/*)#abcdefgh',GENESIS),('tr('+'K'+'A'*51+')#abcdefgh',GENESIS),('tr(tpub'+'A'*100+'/0/*)#abcdefgh','c'*64)]:
            declaration={'network':'signet','genesis':genesis,'wallets':[
                {'member_id':'op-rounds','wallet_kind':'rounds','identity':'rounds-wallet','descriptors':[descriptor]}]}
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'wallets.json';path.write_text(json.dumps(declaration))
                with self.subTest(descriptor=descriptor[:10]),patch.object(bridge,'WALLET_DECLARATIONS',path),patch.object(bridge,'ops') as operation,self.assertRaises(ValueError):
                    bridge.public_wallet_declarations(GENESIS)
                operation.assert_not_called()

    def test_healthy_containers_and_positive_pool_do_not_authorize_dispatch(self):
        chain={'chain':'signet','bestblockhash':TIP,'blocks':123,'initialblockdownload':False}
        candidates={'truncated_tables':[],'rows':{'pool':[{'amount':100000,'expiry_height':140}]},'complete':False}
        with patch.object(bridge,'ops',side_effect=[json.dumps(chain),GENESIS,json.dumps(chain)]),patch.object(bridge,'disk_financial_config',return_value={'estimator_max_fee_rate_sat_kvb':1000}),patch.object(bridge,'run',return_value='{"status":"running"}'),patch.object(bridge,'public_wallet_declarations',return_value={'available':True}),patch.object(bridge,'claimant_inventory',return_value=candidates):
            result=bridge.financial_readiness()
        self.assertFalse(result['complete']);self.assertFalse(result['qualified'])
        for key in ('effective_fee_ceilings_sat_kvb','available_fee_funding_msat','capital_exposure_msat','budget_remaining_msat','pool_ready','channel_route_ready'):
            self.assertIsNone(result[key])
        self.assertIn('runtime_wallet_inventory_with_canonical_positions_and_active_input_locks',result['gaps'])
        self.assertIsNone(bridge.READ_DEADLINE)

    def test_single_total_deadline_applies_across_all_subprocess_reads(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(bridge,'DEPLOY',Path(folder)):
            with patch.object(bridge,'READ_DEADLINE',bridge.time.monotonic()+0.02),self.assertRaises(ValueError):
                bridge.run([sys.executable,'-c','import time; time.sleep(1)'])
        with patch.object(bridge,'READ_DEADLINE',10.0),patch.object(bridge.time,'monotonic',return_value=11.0),patch.object(bridge.subprocess,'Popen') as child,self.assertRaises(ValueError):
            bridge.run(['fixed-read'])
        child.assert_not_called()

    def test_subprocess_output_bound_stops_large_child_before_capture_allocation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(bridge,'DEPLOY',Path(folder)),self.assertRaisesRegex(ValueError,'output bound'):
            bridge.run([sys.executable,'-c','import sys; sys.stdout.write("x"*4000000)'])

    def test_snapshot_does_not_reveal_wallet_addresses(self):
        chain={'chain':'signet','bestblockhash':TIP,'blocks':123,'initialblockdownload':False}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state/signet-fallback';state.mkdir(parents=True)
            (state/'captaind.toml').write_text('network="signet"\n')
            with patch.object(bridge,'DEPLOY',root),patch.object(bridge,'ops',side_effect=[json.dumps(chain),json.dumps(chain),GENESIS]) as operation,patch.object(bridge,'run',return_value='{"status":"running"}'):
                result=bridge.collect('snapshot','')
        self.assertIsNone(result['wallet']);self.assertFalse(result['wallet_inventory_complete'])
        self.assertNotIn(('capt','rpc','wallet'),[call.args for call in operation.call_args_list])


if __name__=='__main__':unittest.main()
