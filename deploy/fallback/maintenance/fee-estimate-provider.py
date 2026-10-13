#!/usr/bin/env python3
"""Signet-only, temporary RPC estimate control; never retry an upstream call.

check performs two read-only identity probes without opening a listener.
serve requires a declared profile and fresh global simulator pause. It changes
only a successful estimatesmartfee response for the selected target/mode.
All other RPC response bytes/status pass through unchanged, including errors.
No request bodies, auth, secrets or payment outcomes enter its journal.
"""
import argparse
import base64
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GENESIS = '00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6'
HEAD = '8f2286ee7e1631b0ceccd1631ec3b728a1bb0a52'
MAX_BODY = 16*1024*1024
MAX_REPLY = 32*1024*1024


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def private_ipv4(value):
    ip = ipaddress.IPv4Address(value)
    networks = ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']
    assert any(ip in ipaddress.IPv4Network(n) for n in networks), 'explicit private/loopback IPv4 required'
    return str(ip)


class Provider:
    def __init__(self, host, port, cookie, *, mode, rate, seconds, journal=None, clock=time.monotonic):
        self.host = private_ipv4(host)
        assert 1 <= port <= 65535 and mode in ('payout', 'sweep')
        assert type(rate) is int and 1 <= rate <= 500 and 1 <= seconds <= 600
        self.port, self.cookie = port, Path(cookie)
        self.mode, self.rate, self.clock = mode, rate, clock
        self.deadline = clock()+seconds
        self.journal, self.lock = journal, threading.Lock()

    def authorization(self):
        assert self.cookie.is_file() and self.cookie.stat().st_mode & 0o077 == 0
        raw = self.cookie.read_bytes().strip()
        assert 0 < len(raw) <= 4096 and b':' in raw and b'\n' not in raw
        return 'Basic '+base64.b64encode(raw).decode('ascii')

    def upstream(self, body):
        # http.client ignores HTTP_PROXY/HTTPS_PROXY; credentials stay at the
        # fixed declared private endpoint. A lost reply is never redispatched.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=8)
        try:
            connection.request('POST', '/', body=body, headers={
                'Authorization': self.authorization(), 'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(MAX_REPLY+1)
            assert len(raw) <= MAX_REPLY, 'bounded upstream response'
            return response.status, raw
        finally:
            connection.close()

    def identity(self):
        results = []
        for method, params in [('getblockchaininfo', []), ('getblockhash', [0])]:
            status, raw = self.upstream(json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode())
            reply = json.loads(raw)
            assert status == 200 and not reply.get('error')
            results.append(reply['result'])
        assert results[0]['chain'] == 'signet' and results[1] == GENESIS, 'official signet only'
        return {'utc':utc(), 'network':'signet','genesis':GENESIS,'height':results[0]['blocks']}

    def event(self, value):
        if self.journal is None:
            return
        # Only our whitelisted estimate/control fields; never RPC bodies/ids.
        with self.lock:
            with self.journal.open('ab') as stream:
                assert stream.tell() < 4*1024*1024, 'bounded estimate journal'
                stream.write((json.dumps({'utc':utc(), **value})+'\n').encode())
                stream.flush(); os.fsync(stream.fileno())

    def response(self, body):
        status, raw = self.upstream(body)
        try:
            request = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return status, raw
        if not isinstance(request, dict):
            return status, raw  # Batches pass through without any estimate override.
        params = request.get('params')
        target = 6 if self.mode == 'payout' else 3
        selected = (request.get('method')=='estimatesmartfee' and isinstance(params, list)
                    and len(params)==2 and type(params[0]) is int
                    and params==[target, 'economical'])
        if not selected:
            return status, raw
        if status != 200:
            self.event({'kind':'estimate','mode':self.mode,'target':target,'controlled':False,
                        'original_http_status':status,'presented_sat_vb':None})
            return status, raw
        try:
            original = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return status, raw
        if not isinstance(original, dict):
            return status, raw
        result = original.get('result')
        controlled = (self.clock() < self.deadline and not original.get('error')
                      and original.get('id') == request.get('id') and isinstance(result,dict))
        self.event({'kind':'estimate', 'mode':self.mode,'target':target,
                    'controlled':controlled,'original_http_status':status,
                    'original_feerate_btc_kvb':result.get('feerate') if isinstance(result,dict) else None,
                    'presented_sat_vb':self.rate if controlled else None})
        if not controlled:
            return status, raw
        # Even a missing baseline estimate may be deliberately replaced by
        # the declared high estimate. This is controlled-provider evidence,
        # never a claim about the independent Core market estimator.
        original['result'] = {'feerate':self.rate/100_000, 'blocks':target}
        return status, json.dumps(original, separators=(',',':')).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass  # No HTTP path, auth, RPC body or request ID logging.

    def answer(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Connection','close')
        self.end_headers(); self.wfile.write(body); self.close_connection = True

    def do_POST(self):
        self.connection.settimeout(10)
        provider = self.server.provider
        try:
            auth = self.headers.get_all('Authorization', [])
            if len(auth)!=1 or not hmac.compare_digest(auth[0], provider.authorization()):
                return self.answer(401, b'{"error":"unauthorized"}')
            assert self.path=='/' and not self.headers.get('Transfer-Encoding')
            lengths = self.headers.get_all('Content-Length', [])
            assert len(lengths)==1 and lengths[0].isdigit()
            length = int(lengths[0]); assert 0 < length <= MAX_BODY
            body = self.rfile.read(length); assert len(body)==length
            status, response = provider.response(body)
            self.answer(status,response)
        except Exception:
            # No retry, fabricated node status, signed replacement or money
            # outcome. The caller must retain and reconcile any uncertainty.
            self.answer(503,b'{"error":"provider unavailable; no automatic retry"}')


def declared(request, coordination):
    assert request['target_vm']=='172.81.182.157' and request['head']==HEAD
    assert re.fullmatch('[a-zA-Z0-9_-]{1,64}',request['run_id'])
    assert type(request['operator_fee_budget_sat']) is int and request['operator_fee_budget_sat']>=0
    assert request['budget_enforced_by_proxy'] is False
    assert request['signed_transactions_survive_window'] is True
    assert coordination['simulator_dispatch_paused'] is True
    assert coordination['target_vm']==request['target_vm'] and coordination['target_head']==HEAD
    assert all(type(coordination[k]) is int and coordination[k]==0 for k in ('inflight_actions','unknown_actions',
        'recovery_inflight_or_unknown','funding_inflight_or_unknown'))
    observed=datetime.datetime.fromisoformat(coordination['observed_utc'])
    assert observed.tzinfo is not None
    age=(datetime.datetime.now(datetime.timezone.utc)-observed).total_seconds()
    assert 0 <= age < 600, 'fresh independent pause required'


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('check','serve'))
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--coordination',type=Path)
    args=parser.parse_args()
    request=json.loads(args.request.read_text())
    provider=Provider(request['backend_host'],request['backend_port'],request['cookie_file'],
        mode=request['estimate_mode'],rate=request['sat_vb'],seconds=request['seconds'])
    identity=provider.identity()
    if args.mode=='check':
        print(json.dumps({**identity,'result':'READ_ONLY_BACKEND_IDENTITY_PASS',
                          'listener_open':False,'estimate_override_dispatched':False}))
        return
    assert args.coordination is not None
    coordination=json.loads(args.coordination.read_text()); declared(request,coordination)
    assert 1024 <= request['listen_port'] <= 65535
    address=(private_ipv4(request['listen_host']),request['listen_port'])
    directory=Path(request['evidence_directory']); directory.mkdir(mode=0o700,exist_ok=False)
    provider.journal=directory/'estimates.jsonl'
    provider.event({'kind':'declared_control','run_id':request['run_id'],'mode':provider.mode,
        'sat_vb':provider.rate,'seconds':request['seconds'], 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'request_sha256':hashlib.sha256(args.request.read_bytes()).hexdigest(),
        'coordination_sha256':hashlib.sha256(args.coordination.read_bytes()).hexdigest(),
        'budget_enforced_by_proxy':False,'signed_transactions_survive_window':True})
    server=ThreadingHTTPServer(address, Handler); server.provider=provider
    provider.deadline = provider.clock()+request['seconds']
    print(json.dumps({**identity,'result':'CONTROL_LISTENER_READY_NOT_DAEMON_QUALIFIED',
        'pid':os.getpid(),'address':list(address),'mode':provider.mode,'sat_vb':provider.rate,
        'seconds':request['seconds'],'budget_enforced_by_proxy':False}),flush=True)
    # When the interval expires responses automatically become unmodified.
    # Keep forwarding until the lead restores native URLs and stops this PID.
    server.serve_forever()


if __name__=='__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'result':'PROVIDER_REFUSED_OR_UNCERTAIN','money_outcome':'unqualified'}))
        raise SystemExit(1)
