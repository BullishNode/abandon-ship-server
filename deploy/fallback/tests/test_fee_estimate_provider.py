import copy
import datetime
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPEC=importlib.util.spec_from_file_location('fee_provider',Path(__file__).parents[1]/'maintenance/fee-estimate-provider.py')
MODULE=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class Core(BaseHTTPRequestHandler):
    def log_message(self,*_args):
        pass

    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']))
        self.server.calls.append(body)
        try:
            req=json.loads(body)
        except ValueError:
            req={}
        if isinstance(req,list):
            result=b'[{"result":"unchanged batch","id":2}]';status=200
        elif req.get('method')=='getblockchaininfo':
            result=json.dumps({'result':{'chain':self.server.chain,'blocks':326171},'id':req.get('id')}).encode();status=200
        elif req.get('method')=='getblockhash':
            result=json.dumps({'result':MODULE.GENESIS,'id':req.get('id')}).encode();status=200
        elif req.get('method')=='sendrawtransaction':
            result=b'{"error":{"code":-25,"message":"fixture rejected"},"id":4}';status=500
        else:
            result=json.dumps({'result':{'feerate':0.00002,'blocks':3},'error':None,'id':req.get('id')}).encode();status=200
        self.send_response(status);self.send_header('Content-Length',str(len(result)))
        self.end_headers();self.wfile.write(result)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cookie=self.root/'cookie';self.cookie.write_text('fixture:nonproduction-cookie');self.cookie.chmod(0o600)
        self.core=ThreadingHTTPServer(('127.0.0.1',0),Core);self.core.calls=[];self.core.chain='signet'
        threading.Thread(target=self.core.serve_forever,daemon=True).start()
        self.now=0
        self.provider=MODULE.Provider('127.0.0.1',self.core.server_port,self.cookie,
            mode='payout',rate=50,seconds=30,journal=self.root/'journal',clock=lambda:self.now)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),MODULE.Handler);self.http.provider=self.provider
        threading.Thread(target=self.http.serve_forever,daemon=True).start()

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.core.shutdown();self.core.server_close();self.tmp.cleanup()

    def call(self,request,auth=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
        try:
            body=json.dumps(request).encode()
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']=self.provider.authorization()
            connection.request('POST','/',body,headers)
            reply=connection.getresponse();return reply.status,reply.read()
        finally:
            connection.close()

    def test_only_payout_target_is_changed_and_expiry_restores_original(self):
        req={'jsonrpc':'2.0','id':3,'method':'estimatesmartfee','params':[6,'economical']}
        status,raw=self.call(req);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['result']['feerate'],0.0005)
        req['params']=[3,'economical'];_,raw=self.call(req);self.assertEqual(json.loads(raw)['result']['feerate'],0.00002)
        req['params']=[6,'economical'];self.now=30
        _,raw=self.call(req);self.assertEqual(json.loads(raw)['result']['feerate'],0.00002)
        self.assertEqual(len(self.core.calls),3)

    def test_sweep_mode_does_not_change_payout_estimate(self):
        self.provider.mode='sweep'
        req={'id':7,'method':'estimatesmartfee','params':[3,'economical']}
        _,raw=self.call(req);self.assertEqual(json.loads(raw)['result']['feerate'],0.0005)
        req['params']=[6,'economical'];_,raw=self.call(req)
        self.assertEqual(json.loads(raw)['result']['feerate'],0.00002)

    def test_no_auth_does_not_reach_core(self):
        status,_=self.call({'id':1,'method':'getblockcount','params':[]},auth=False)
        self.assertEqual(status,401);self.assertEqual(self.core.calls,[])

    def test_money_error_bytes_status_pass_through_once(self):
        status,raw=self.call({'id':4,'method':'sendrawtransaction','params':['not-a-real-transaction']})
        self.assertEqual(status,500)
        self.assertEqual(raw,b'{"error":{"code":-25,"message":"fixture rejected"},"id":4}')
        self.assertEqual(len(self.core.calls),1)

    def test_transport_failure_is_not_retried_or_reported_as_success(self):
        called=[]
        def outage(body):
            called.append(body);raise TimeoutError('fixture lost reply')
        self.provider.upstream=outage
        status,raw=self.call({'id':1,'method':'sendrawtransaction','params':['retain-original-identity']})
        self.assertEqual(status,503);self.assertNotIn(b'result',raw);self.assertEqual(len(called),1)

    def test_batch_is_not_rewritten(self):
        _,raw=self.call([{'id':2,'method':'estimatesmartfee','params':[6,'economical']}])
        self.assertEqual(raw,b'[{"result":"unchanged batch","id":2}]')

    def test_private_request_fields_are_not_journaled(self):
        req={'id':'private-request-marker','method':'estimatesmartfee','params':[6,'economical']}
        self.call(req)
        journal=(self.root/'journal').read_text()
        self.assertNotIn('private-request-marker',journal)
        self.assertNotIn('nonproduction-cookie',journal)
        self.assertNotIn('Authorization',journal)

    def test_identity_rejects_mainnet_before_control(self):
        self.assertEqual(self.provider.identity()['genesis'],MODULE.GENESIS)
        self.core.chain='main'
        with self.assertRaises(AssertionError):self.provider.identity()

    def test_public_endpoint_is_refused(self):
        for address in ['0.0.0.0','8.8.8.8','::1']:
            with self.assertRaises((AssertionError,ValueError)):MODULE.private_ipv4(address)

    def test_stale_nonzero_or_boolean_pause_counts_are_refused(self):
        request={'target_vm':'172.81.182.157','head':MODULE.HEAD,'run_id':'fee-1',
                 'operator_fee_budget_sat':5000,'budget_enforced_by_proxy':False,'signed_transactions_survive_window':True}
        pause={'target_vm':'172.81.182.157','target_head':MODULE.HEAD,'simulator_dispatch_paused':True,
               'inflight_actions':0,'unknown_actions':0,'recovery_inflight_or_unknown':0,'funding_inflight_or_unknown':0,
               'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        MODULE.declared(request,pause)
        for key,value in [('unknown_actions',1),('funding_inflight_or_unknown',False),
            ('observed_utc',(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=1)).isoformat())]:
            bad=copy.deepcopy(pause);bad[key]=value
            with self.assertRaises(AssertionError):MODULE.declared(request,bad)


if __name__=='__main__':
    unittest.main()
