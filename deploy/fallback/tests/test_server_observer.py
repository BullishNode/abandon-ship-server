import importlib.util,json,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('server_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class BridgeTest(unittest.TestCase):
    def test_untrusted_request_cannot_select_mutations_or_sql(self):
        for request in [ {'kind':'cli','method':'sendtoaddress'}, {'kind':'coins','after':"'; DROP TABLE vtxo;--"},
                         {'kind':'coins','after':'$(touch /tmp/x)'}, {'kind':'snapshot','after':'coin'},
                         {'kind':'snapshot','command':'sudo docker exec'}, {'kind':'coins','after':[]}, [],
                         {'kind':'snapshot','x':'a'*5000} ]:
            with self.subTest(request=request), self.assertRaises((ValueError,TypeError)):
                bridge.request(json.dumps(request).encode())
    def test_inventory_is_read_only_bounded_and_not_a_commit_watermark(self):
        rows=[{'id':str(i)} for i in range(101)]
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n'+json.dumps(rows)+'\nCOMMIT\n') as ops:
            r=bridge.page('settlements','abc:1')
        self.assertEqual(100,len(r['rows']));self.assertEqual('99',r['next'])
        self.assertFalse(r['inventory_consistent'])
        args=ops.call_args.args
        self.assertEqual(args[0],'sql');self.assertTrue(args[1].startswith('BEGIN READ ONLY;'))
        self.assertIn('LIMIT 101',args[1]);self.assertIn("statement_timeout='8s'",args[1])
    def test_complete_page_has_no_cursor_and_empty_is_not_unknown(self):
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n[]\nCOMMIT\n'):
            r=bridge.page('settlements','')
        self.assertEqual([],r['rows']);self.assertIsNone(r['next'])
    def test_malformed_database_response_fails_closed(self):
        with patch.object(bridge,'ops',return_value='BEGIN\nnot-json\nCOMMIT\n'),self.assertRaises(ValueError):
            bridge.page('coins','')
if __name__=='__main__':unittest.main()

class BindingBridgeTest(unittest.TestCase):
    def test_binding_scope_is_public_bounded_and_fixed(self):
        req={'kind':'binding_check','mailboxes':['02'+'1'*64],'keys':['03'+'2'*64]}
        kind,arg=bridge.request(json.dumps(req).encode())
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n{"records":[],"links":[]}\nCOMMIT\n') as ops:
            value=bridge.collect(kind,arg)
        self.assertTrue(value['inventory_consistent']);self.assertEqual(req['keys'],value['keys'])
        sql=ops.call_args.args[1]
        self.assertIn('REPEATABLE READ READ ONLY',sql);self.assertIn("statement_timeout='8s'",sql)
        self.assertIn('WHERE user_pubkey=ANY',sql)
    def test_binding_requests_cannot_smuggle_sql_or_unbounded_scans(self):
        for field,value in [('keys',['02'+'1'*64]*33),('keys',["'; SELECT pg_sleep(99); --"]),('mailboxes',[]),('mailboxes',['02'+'1'*64]*3),('keys',[{}])]:
            req={'kind':'binding_check','mailboxes':['02'+'1'*64],'keys':[]};req[field]=value
            with self.subTest(field=field,value=value),self.assertRaises((ValueError,TypeError)):
                bridge.request(json.dumps(req).encode())

class ImmutableBindingBridgeTest(unittest.TestCase):
    def test_v3_public_record_and_child_queries_have_no_mutable_fields(self):
        req={'kind':'binding_check','mailboxes':['02'+'1'*64],'keys':['03'+'2'*64]}
        kind,arg=bridge.request(json.dumps(req).encode())
        with patch.object(bridge,'ops',return_value='BEGIN\nSET\n{"records":[],"links":[]}\nCOMMIT\n') as ops:
            result=bridge.collect(kind,arg)
        sql=ops.call_args.args[1]
        self.assertEqual(result['fallback_protocol'],3)
        for field in ['account_xpub','mailbox_sig','account_sig','child_index']:
            self.assertIn(field,sql)
        self.assertNotIn('seq',sql)
        self.assertNotIn('encode(sig,',sql)
        self.assertIn('REPEATABLE READ READ ONLY',sql)

    def test_old_schema_failure_is_never_an_empty_valid_account_inventory(self):
        for kind in ['records','links']:
            with self.subTest(kind=kind),patch.object(bridge,'ops',side_effect=RuntimeError('missing v3 column')):
                with self.assertRaises(RuntimeError):
                    bridge.page(kind,'')
