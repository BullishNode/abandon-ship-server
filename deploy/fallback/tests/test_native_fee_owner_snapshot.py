import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE=Path(__file__).resolve().parents[1]/'server-observer.py'
spec=importlib.util.spec_from_file_location('fee_owner_bridge',SOURCE)
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


def snapshot():
    return {'schema':1,'policy_sha256':'1'*64,'opening_sha256':'2'*64,'journal_sha256':'3'*64,
        'journal_device':1,'journal_inode':2,'journal_bytes':100,
        'policy':{'urgent_reserve_sat':15000,'ordinary_budget_sat':60000,
            'external_future_reserved_sat':20000,'opening_sha256':None},
        'opening':{'version':1,'network':'signet','genesis':'4'*64,'server_pubkey':'02'+'5'*64,
            'history_prefix_bytes':0,'history_prefix_sha256':'6'*64,'native_spent_sat':0,
            'external_spent_sat':0,'accounting_receipt_sha256':None},
        'charged_sat':21500,'remaining_total_sat':38500,'urgent_remaining_sat':14000,
        'discretionary_remaining_sat':24500,'direct_attempt_count':0,'direct_reserved_sat':0,
        'funding_reserved_sat':500,'round_reserved_sat':500,'pool_reserved_sat':0,'urgent_reserved_sat':1000,
        'funding':[{'index':1,'purpose':'round','family_sha256':'7'*64,'generation':0,
            'unsigned_body_sha256':'8'*64,'prevouts_sha256':'9'*64,'txid':'a'*64,
            'signed_wtxid':'b'*64,'ceiling_sat':500}],
        'urgent':[{'index':1,'ceiling_sat':1000,'source_binding_sha256':'c'*64,
            'transactions':[{'txid':'d'*64,'wtxid':'e'*64}]}],
        'resolutions':[{'winner_index':1,'block_height':100,'block_hash':'f'*64}]}


class NativeFeeOwnerSnapshot(unittest.TestCase):
    def test_fixed_operation_accepts_no_caller_parameters(self):
        kind='native_fee_owner_snapshot'
        self.assertEqual(bridge.request(json.dumps({'kind':kind})),(kind,None))
        for field in ('addr','service','path','after','method','command'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                bridge.request(json.dumps({'kind':kind,field:'not accepted'}))

    def test_public_rows_survive_exactly_without_becoming_readiness(self):
        source=snapshot();raw=json.dumps(source)+'\n'
        with patch.object(bridge,'run',return_value=raw)as run:
            result=bridge.collect('native_fee_owner_snapshot',None)
        self.assertEqual(result['owner_snapshot'],source)
        self.assertFalse(result['complete']);self.assertFalse(result['qualified'])
        _,container,address=bridge.WALLET_SOURCES['watchman']
        run.assert_called_once_with(['sudo','-n','docker','exec',container,'captaind','rpc',
            '--addr',address,'native-fee-budget'],timeout=12)
        self.assertIn('fresh private SQL',result['validation_scope'])
        self.assertIsNone(bridge.READ_DEADLINE)

    def test_unsigned_and_unbound_rows_are_observed_without_inventing_bindings(self):
        source=snapshot();source['funding'][0]['signed_wtxid']=None
        source['urgent'][0]['source_binding_sha256']=None;source['urgent'][0]['transactions']=[]
        self.assertEqual(bridge.public_native_fee_snapshot(json.dumps(source)),source)

    def test_unknown_private_fields_refuse_at_every_nesting_boundary(self):
        paths=[(),('policy',),('opening',),('funding',0),('urgent',0),
            ('urgent',0,'transactions',0),('resolutions',0)]
        for path in paths:
            source=snapshot();row=source
            for part in path:row=row[part]
            row['private_witness']='never export'
            with self.subTest(path=path),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(json.dumps(source))

    def test_missing_fields_refuse_whole_frame(self):
        for path,key in [((),'funding'),(('policy',),'ordinary_budget_sat'),
            (('opening',),'genesis'),(('funding',0),'prevouts_sha256'),
            (('urgent',0),'transactions'),(('urgent',0,'transactions',0),'wtxid'),
            (('resolutions',0),'block_hash')]:
            source=snapshot();row=source
            for part in path:row=row[part]
            del row[key]
            with self.subTest(path=path,key=key),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(json.dumps(source))

    def test_duplicate_json_keys_cannot_override_public_shape(self):
        raw=json.dumps(snapshot())
        for old,new in [('"schema": 1','"schema": 1, "schema": 1'),
            ('"index": 1','"index": 1, "index": 1'),
            ('"network": "signet"','"network": "signet", "network": "signet"')]:
            with self.subTest(old=old),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(raw.replace(old,new))

    def test_numeric_types_ranges_and_nonfinite_values_refuse(self):
        for key in ('schema','charged_sat','journal_inode','journal_bytes'):
            for wrong in (True,-1,0.5,'1',float('nan'),float('inf')):
                source=snapshot();source[key]=wrong
                with self.subTest(key=key,wrong=wrong),self.assertRaises(ValueError):
                    bridge.public_native_fee_snapshot(json.dumps(source))
        source=snapshot();source['journal_bytes']=bridge.MAX_BYTES+1
        with self.assertRaises(ValueError):bridge.public_native_fee_snapshot(json.dumps(source))

    def test_unknown_schema_purpose_network_and_identities_refuse(self):
        for path,key,wrong in [((),'schema',2),(('funding',0),'purpose','other'),
            (('opening',),'network','regtest'),(('opening',),'server_pubkey','tprv-private'),
            (('funding',0),'signed_wtxid','raw private transaction'),
            (('urgent',0,'transactions',0),'txid','A'*64),
            (('resolutions',0),'block_hash',None)]:
            source=snapshot();row=source
            for part in path:row=row[part]
            row[key]=wrong
            with self.subTest(path=path,key=key),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(json.dumps(source))

    def test_complete_collection_bounds_never_truncate_rows(self):
        for key in ('funding','urgent','resolutions'):
            source=snapshot();source[key]*=4097
            with self.subTest(key=key),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(json.dumps(source))
        source=snapshot();source['urgent'][0]['transactions']*=3
        with self.assertRaises(ValueError):bridge.public_native_fee_snapshot(json.dumps(source))

    def test_invalid_or_oversized_native_output_refuses_without_projection(self):
        for raw in ('','[]','{}','invalid',' '*bridge.MAX_BYTES+'{}'):
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):
                bridge.public_native_fee_snapshot(raw)

    def test_failure_and_timeout_restore_deadline_and_never_return_ready(self):
        bridge.READ_DEADLINE=123
        try:
            for error in (subprocess.CalledProcessError(1,['fixed']),
                          subprocess.TimeoutExpired(['fixed'],12),ValueError('bad source')):
                with patch.object(bridge,'run',side_effect=error),self.assertRaises(type(error)):
                    bridge.native_fee_owner_snapshot()
                self.assertEqual(bridge.READ_DEADLINE,123)
        finally:bridge.READ_DEADLINE=None

    def test_extra_outer_observation_fields_are_not_copied_from_native_response(self):
        source=snapshot();source['qualified']=True
        with patch.object(bridge,'run',return_value=json.dumps(source)),self.assertRaises(ValueError):
            bridge.native_fee_owner_snapshot()


if __name__=='__main__':unittest.main()
