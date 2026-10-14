import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('direct_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


def frame():
    return {'snapshot':'1:2:','observed_at':'2026-10-14T14:00:00Z',
        'rows':{name:[] for name in bridge.DIRECT_JOURNAL_COLUMNS}}


class DirectJournalTest(unittest.TestCase):
    def test_binding_inventory_is_scoped_to_allocated_owners(self):
        sql=bridge.direct_journal_sql()
        self.assertIn('expiry_allocation a WHERE a.owner_pubkey=key_link.user_pubkey',sql)
        self.assertIn('a.owner_pubkey=l.user_pubkey WHERE l.mailbox_pk=fallback_record.mailbox_pk',sql)
        with patch.object(bridge,'ops',return_value=json.dumps(frame())):
            result=bridge.direct_journal()
        self.assertEqual('owners_referenced_by_allocations',result['binding_scope'])

    def test_older_inventory_operations_do_not_export_encoded_claim_witnesses(self):
        for kind in ('coins','pending','nursery'):
            with self.subTest(kind=kind),patch.object(bridge,'ops',return_value='[]') as operation:
                bridge.page(kind,'')
                sql=operation.call_args.args[1]
                self.assertNotIn('signed_vtxo',sql)
                self.assertNotIn('raw_tx',sql)
                self.assertNotIn('encode(v.vtxo',sql)
                self.assertNotIn('encode(p.vtxo',sql)
                self.assertNotIn('encode(n.tx',sql)
        self.assertNotIn('signed_vtxo',bridge.claimant_sql())

    def test_fixed_operation_refuses_selected_query_and_cursor(self):
        self.assertEqual(('direct_journal',None),bridge.request(b'{"kind":"direct_journal"}'))
        for field,value in [('sql','SELECT 1'),('after','txid'),('path','/data/seed'),('limit',10000)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                bridge.request(json.dumps({'kind':'direct_journal',field:value}))

    def test_one_complete_public_snapshot_is_not_completed_payment(self):
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n'+json.dumps(frame())+'\nCOMMIT') as operation:
            result=bridge.collect('direct_journal',None)
        self.assertEqual('sql',operation.call_args.args[0])
        sql=operation.call_args.args[1]
        self.assertIn('REPEATABLE READ READ ONLY',sql)
        self.assertIn("statement_timeout='8s'",sql)
        self.assertIn('pg_current_snapshot()',sql)
        for secret in ('signed_tx','source_evidence','preimage','vtxo,'):
            self.assertNotIn(secret,sql)
        self.assertNotIn('WHERE confirmed_height',sql,'pending and all replacement versions must survive')
        self.assertTrue(result['journal_complete']);self.assertTrue(result['inventory_consistent'])
        self.assertFalse(result['complete']);self.assertFalse(result['qualified'])

    def test_truncated_table_is_refused_instead_of_exporting_a_prefix(self):
        for table in bridge.DIRECT_JOURNAL_COLUMNS:
            source=frame();source['rows'][table]=[{}]*2001
            with self.subTest(table=table),patch.object(bridge,'ops',return_value=json.dumps(source)),self.assertRaisesRegex(ValueError,'row budget'):
                bridge.direct_journal()

    def test_unknown_secret_fields_and_nested_fee_witnesses_are_refused(self):
        for table in bridge.DIRECT_JOURNAL_FIELDS:
            source=frame();row={field:None for field in bridge.DIRECT_JOURNAL_FIELDS[table]}
            row['preimage']='never-export-this';source['rows'][table]=[row]
            with self.subTest(table=table),patch.object(bridge,'ops',return_value=json.dumps(source)),self.assertRaises(ValueError):
                bridge.direct_journal()
        source=frame();row={field:None for field in bridge.DIRECT_JOURNAL_FIELDS['carriers']}
        row['fee_inputs']=[{'outpoint':'public','value_sat':1000,'script_pubkey':'0014','witness':'never-export-this'}]
        source['rows']['carriers']=[row]
        with patch.object(bridge,'ops',return_value=json.dumps(source)),self.assertRaisesRegex(ValueError,'fee input fields'):
            bridge.direct_journal()

    def test_missing_schema_and_oversize_are_not_empty_success(self):
        for source in ({},[],{'snapshot':'1:2:','observed_at':'now','rows':{'allocations':[]}}):
            with self.subTest(source=source),patch.object(bridge,'ops',return_value=json.dumps(source)),self.assertRaises(ValueError):
                bridge.direct_journal()
        with patch.object(bridge,'ops',side_effect=subprocess.CalledProcessError(1,['fixed-sql'])),self.assertRaises(subprocess.CalledProcessError):
            bridge.direct_journal()
        source=frame();source['snapshot']='x'*(bridge.MAX_BYTES+1)
        with patch.object(bridge,'ops',return_value=json.dumps(source)),self.assertRaisesRegex(ValueError,'byte budget'):
            bridge.direct_journal()


if __name__=='__main__':unittest.main()
