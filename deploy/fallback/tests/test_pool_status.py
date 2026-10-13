import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('pool_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class PoolStatusTest(unittest.TestCase):
    def fixture(self,anchors=None,backing=None,ending_hash='tip'):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        root=Path(directory.name);(root/'state/signet-fallback').mkdir(parents=True)
        (root/'state/signet-fallback/captaind.toml').write_text('[vtxopool]\nvtxo_pre_expiry=10\n')
        declaration=root/'descriptor.json';declaration.write_text(json.dumps({'genesis':'genesis','network':'signet','server_pubkey':'02'+'12'*32}))
        self.patches=[patch.object(bridge,'DEPLOY',root),patch.object(bridge,'DESCRIPTOR',declaration)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        default={'anchor_txid_le':'11'*32,'anchor_vout_le':'00000000','server_pubkey':'02'+'12'*32,
            'encoding_version':'0200','coin_count':8,'amount_sat':80000,'minimum_expiry':148}
        inventory={'unspent_count':24,'anchors':[default] if anchors is None else anchors}
        calls=[]
        def ops(*args):
            calls.append(args)
            if args==('cli','getblockhash','0'):return '"genesis"\n'
            if args==('cli','getblockchaininfo'):
                return json.dumps({'blocks':100,'chain':'signet','bestblockhash':ending_hash if len([c for c in calls if c==args])==2 else 'tip'})
            if args[0]=='sql':
                self.assertIn('REPEATABLE READ READ ONLY',args[1]);self.assertIn("statement_timeout='8s'",args[1]);self.assertIn('expiry_height>110',args[1])
                self.assertNotIn("encode(vtxo,'hex')",args[1])
                return 'BEGIN\nSET\n'+json.dumps(inventory)+'\nCOMMIT\n'
            if args==('capt','rpc','wallet'):
                return json.dumps({'rounds':{'trusted_balance':100000,'confirmed_utxos':['aa'*32+':1']}})
            if args==('cli','gettxout','11'*32,'0','true'):
                return json.dumps({'confirmations':3,'value':'0.00080000'} if backing is None else backing)
            if args==('cli','gettxout','aa'*32,'1','true'):
                return '{"confirmations":2,"value":0.00100000}'
            raise AssertionError('unexpected or mutating command')
        return ops,calls,default

    def test_exact_bounded_confirmed_snapshot_and_public_response(self):
        ops,calls,_=self.fixture()
        with patch.object(bridge,'ops',side_effect=ops):result=bridge.collect('pool_status',None)
        self.assertTrue(result['complete']);self.assertTrue(result['stable_tip'])
        self.assertEqual(8,result['pool']['confirmed_eligible_count'])
        self.assertEqual(80000,result['pool']['confirmed_eligible_amount_sat'])
        self.assertEqual(100000,result['rounds']['confirmed_balance_sat'])
        self.assertNotIn('signed_vtxo',json.dumps(result));self.assertNotIn('rpc_pass',json.dumps(result))

    def test_pool_request_cannot_select_paths_queries_or_mutations(self):
        self.assertEqual(('pool_status',None),bridge.request(b'{"kind":"pool_status"}'))
        for name in ['after','sql','path','wallet','keys','method']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                bridge.request(json.dumps({'kind':'pool_status',name:'anything'}).encode())

    def test_no_backing_confirmation_or_insufficient_anchor_is_not_usable(self):
        for backing in [False,{'confirmations':0,'value':'0.00080000'},{'confirmations':2,'value':'0.00070000'}]:
            ops,_,_=self.fixture(backing=backing)
            with patch.object(bridge,'ops',side_effect=ops):result=bridge.pool_status()
            self.assertEqual(8,result['pool']['eligible_count']);self.assertEqual(0,result['pool']['confirmed_eligible_count'])

    def test_empty_and_changed_tip_do_not_manufacture_positive_pool(self):
        ops,_,_=self.fixture(anchors=[],ending_hash='newtip')
        with patch.object(bridge,'ops',side_effect=ops):result=bridge.pool_status()
        self.assertEqual(0,result['pool']['confirmed_eligible_count']);self.assertIsNone(result['pool']['minimum_eligible_expiry_height'])
        self.assertFalse(result['stable_tip'])

    def test_identity_encoding_and_oversized_anchor_inventory_refused(self):
        for change in [{'server_pubkey':'03'+'34'*32},{'encoding_version':'0300'}]:
            ops,_,default=self.fixture();default.update(change)
            with patch.object(bridge,'ops',side_effect=ops),self.assertRaises(ValueError):bridge.pool_status()
        ops,_,default=self.fixture();ops,_,_=self.fixture(anchors=[default]*33)
        with patch.object(bridge,'ops',side_effect=ops),self.assertRaises(ValueError):bridge.pool_status()

if __name__=='__main__':unittest.main()
