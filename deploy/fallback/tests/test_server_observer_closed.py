"""Synthetic fixed-source controls; no live readiness qualification."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('closed_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

def frame():
    names=bridge.FINANCIAL_TABLES|{'journal_'+name for name in bridge.DIRECT_JOURNAL_COLUMNS}|{'nursery','rounds'}
    return {'snapshot':'1:2:','observed_at':'2026-10-15T15:00:00Z','rows':{name:[] for name in names}}

class ClosedFrontierTests(unittest.TestCase):
    def test_fixed_no_argument_source_is_complete_only_for_database_tables(self):
        self.assertEqual(('closed_frontier',None),bridge.request(b'{"kind":"closed_frontier"}'))
        with patch.object(bridge,'ops',return_value=json.dumps(frame())) as call:
            result=bridge.collect('closed_frontier',None)
        sql=call.call_args.args[1]
        self.assertIn('REPEATABLE READ READ ONLY',sql);self.assertIn('pg_current_snapshot()',sql)
        self.assertIn("statement_timeout='8s'",sql);self.assertIn('onchain_spent_txid FROM vtxo WHERE true',sql)
        self.assertIn('FROM expiry_carrier WHERE true',sql);self.assertIn('FROM nursery_tx WHERE true',sql)
        self.assertIn('FROM public.round WHERE true',sql)
        for private in ('signed_tx','source_evidence','encode(vtxo','unlock_preimage AS','funding_tx,'):
            self.assertNotIn(private,sql)
        self.assertTrue(result['database_tables_complete']);self.assertFalse(result['complete']);self.assertFalse(result['qualified'])
        for key in ('sql','after','limit'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                bridge.request(json.dumps({'kind':'closed_frontier',key:'arbitrary'}))
    def test_missing_tables_and_unknown_secret_fields_refuse(self):
        bad=frame();del bad['rows']['nursery']
        with patch.object(bridge,'ops',return_value=json.dumps(bad)),self.assertRaises(ValueError):bridge.closed_frontier()
        bad=frame();bad['rows']['coins']=[{'preimage':'not-public'}]
        with patch.object(bridge,'ops',return_value=json.dumps(bad)),self.assertRaises(ValueError):bridge.closed_frontier()
    def test_overrun_refuses_whole_frame_instead_of_returning_prefix(self):
        bad=frame();bad['rows']['coins']=[{}]*2001
        with patch.object(bridge,'ops',return_value=json.dumps(bad)),self.assertRaisesRegex(ValueError,'row budget'):bridge.closed_frontier()
        bad=frame();bad['snapshot']='x'*(bridge.MAX_BYTES+1)
        with patch.object(bridge,'ops',return_value=json.dumps(bad)),self.assertRaisesRegex(ValueError,'byte budget'):bridge.closed_frontier()

if __name__=='__main__':unittest.main()
