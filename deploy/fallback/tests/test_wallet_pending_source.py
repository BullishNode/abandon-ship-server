"""Original native CLI frames; mocked Core responses test bridge policy only."""
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('pending_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
FIXTURES=Path(__file__).with_name('fixtures')/'wallet-inventory'


def native():return json.loads((FIXTURES/'native-rounds-pending.json').read_text())


def core_result(row):
    # These retained positive frames each have one input. This is a response
    # stub, not an independent Bitcoin decoder or monetary qualification.
    body=bytes.fromhex(row['transaction_body'])
    assert body[4]==1
    return {'txid':row['txid'],'hash':row['txid'],'weight':4*len(body),
        'vin':[{'txid':body[5:37][::-1].hex(),'vout':int.from_bytes(body[37:41],'little'),'scriptSig':{'hex':''}}]}


def core(*args):
    if args[:2]==('cli','getdescriptorinfo'):
        return json.dumps({'hasprivatekeys':False,'isrange':True,'descriptor':args[2]})
    if args[:2]!=('cli','decoderawtransaction'):raise AssertionError('unexpected operation')
    rows=native()['rounds'];row=next(r for r in rows['pending_transactions']+rows['pending_parents'] if r['transaction_body']==args[2])
    return json.dumps(core_result(row))


class WalletPendingSourceTest(unittest.TestCase):
    def test_actual_pending_source_is_preserved_as_separate_limited_wallet_facts(self):
        value=native()
        with patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops',side_effect=core) as calls:
            result=bridge.owning_wallet_inventory('rounds','regtest')
        self.assertTrue(result['available'])
        self.assertEqual(result['source'],value)
        self.assertFalse(result['source']['complete'])
        self.assertEqual(len([c for c in calls.call_args_list if c.args[1]=='decoderawtransaction']),2)

    def test_missing_parent_is_an_explicit_gap_never_an_empty_complete_snapshot(self):
        value=native();w=value['rounds'];w['complete']=False
        w['unavailable_parent_txids']=[w['pending_parents'].pop()['txid']]
        with patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops',side_effect=core):
            result=bridge.owning_wallet_inventory('rounds','regtest')
        self.assertTrue(result['available'])
        self.assertFalse(result['source']['rounds']['complete'])
        self.assertEqual(result['source'],value)

    def test_aggregate_row_limit_includes_wallet_reservations(self):
        value=json.loads((FIXTURES/'native-rounds.json').read_text());w=value['rounds'];row=w['utxos'][0]
        w['utxos']=[dict(row,outpoint=f'{i:064x}:0') for i in range(2000)]
        w['reserved_outpoints']=['f'*64+':0']
        with self.assertRaisesRegex(ValueError,'aggregate'):
            bridge.validate_wallet_frame(value,'rounds','regtest')

    def test_private_row_extension_is_refused_before_any_core_call(self):
        value=native();value['rounds']['pending_transactions'][0]['witness']='synthetic-private-material'
        with patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops') as operation:
            result=bridge.owning_wallet_inventory('rounds','regtest')
        self.assertFalse(result['available']);self.assertIsNone(result['source']);operation.assert_not_called()
        self.assertNotIn('synthetic-private-material',json.dumps(result))

    def test_witness_scriptsig_identity_or_impossible_weight_refuses_whole_source(self):
        mutations=[lambda r:r.update(hash='f'*64),lambda r:r['vin'][0].update(txinwitness=['00']),
            lambda r:r['vin'][0]['scriptSig'].update(hex='00'),lambda r:r.update(weight=1)]
        for mutate in mutations:
            def unsafe(*args):
                result=json.loads(core(*args))
                if args[1]=='decoderawtransaction':mutate(result)
                return json.dumps(result)
            with self.subTest(case=mutate),patch.object(bridge,'run',return_value=json.dumps(native())),patch.object(bridge,'ops',side_effect=unsafe):
                result=bridge.owning_wallet_inventory('rounds','regtest')
            self.assertFalse(result['available']);self.assertIsNone(result['source'])

    def test_unannounced_parent_or_false_complete_flag_is_not_accepted(self):
        for mode in ['missing','extra','false-complete','encoding']:
            value=native();w=value['rounds']
            if mode=='missing':w['pending_parents']=[]
            elif mode=='extra':w['unavailable_parent_txids']=['f'*64];w['complete']=False
            elif mode=='false-complete':w['complete']=False
            else:w['pending_transactions'][0]['encoding']='untrusted-v2'
            with self.subTest(mode=mode),patch.object(bridge,'run',return_value=json.dumps(value)),patch.object(bridge,'ops',side_effect=core):
                result=bridge.owning_wallet_inventory('rounds','regtest')
            self.assertFalse(result['available']);self.assertIsNone(result['source'])


if __name__=='__main__':unittest.main()
