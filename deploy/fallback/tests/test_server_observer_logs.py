import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bridge_logs', Path(__file__).resolve().parents[1]/'server-observer.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class LogsTest(unittest.TestCase):
    def test_request_rejects_arbitrary_commands_services_paths_and_bounds(self):
        for value in [
            {'kind':'logs','service':'captaind; touch /tmp/x'},
            {'kind':'logs','service':'postgres'},
            {'kind':'logs','service':'captaind','path':'/etc/shadow'},
            {'kind':'logs','service':'captaind','seconds':86401},
            {'kind':'logs','service':'captaind','seconds':True},
            {'kind':'logs','service':'watchmand','tail':1001},
            {'kind':'logs','service':'captaind','max_bytes':0},
            {'kind':'logs','service':'captaind','max_bytes':262145},
            {'kind':'logs','service':'captaind','after':'0'},
            {'kind':'rounds_descriptor','path':'/etc/shadow'},
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.request(json.dumps(value).encode())

    def capture(self, source, request):
        popen = subprocess.Popen
        invoked = []
        def child(command, **kwargs):
            invoked.append(command)
            return popen([sys.executable, '-c', source], **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(bridge,'DEPLOY',Path(folder)), \
                patch.object(bridge.subprocess,'Popen',side_effect=child):
            kind, value = bridge.request(json.dumps(request).encode())
            result = bridge.collect(kind, value)
        self.assertEqual(1, len(invoked))
        command = invoked[0]
        self.assertEqual(['sudo','-n','docker','logs','--since'], command[:5])
        self.assertEqual('abandon-signet-fallback-fb-'+request['service']+'-1', command[-1])
        self.assertNotIn('--follow', command)
        return result

    def test_preserves_tick_evidence_and_redacts_full_secret_fields_and_text(self):
        payload = json.dumps({'message':'expiry payout tick summary','waiting':7,'success':True,
                             'mnemonic':'one two three four five six seven eight nine ten eleven twelve'})
        result = self.capture('print('+repr(payload)+'); print("mnemonic: one two three four")',
                              {'kind':'logs','service':'captaind'})
        row = json.loads(result['lines'][0])
        self.assertEqual(7, row['waiting'])
        self.assertEqual('expiry payout tick summary', row['message'])
        self.assertEqual('[redacted]', row['mnemonic'])
        self.assertEqual('[redacted sensitive text]', result['lines'][1])
        self.assertFalse(result['truncated'])
        self.assertFalse(result['timed_out'])
        self.assertFalse(result['complete'])

    def test_real_large_child_output_is_bounded_and_marked_truncated(self):
        result = self.capture('import os; os.write(1,b"x"*1000000)',
                              {'kind':'logs','service':'watchmand','max_bytes':4096})
        self.assertEqual(4096, result['source_bytes'])
        self.assertTrue(result['truncated'])
        self.assertEqual(4096, len(result['lines'][0]))
        self.assertFalse(result['complete'])

    def test_signed_transaction_and_witness_fields_never_enter_public_log_trace(self):
        payload=json.dumps({'message':'wallet signed transaction','txid':'a'*64,
            'raw_tx':'0200000001'+'11'*32,'signed_tx':'0200000001'+'22'*32,
            'details':{'txinwitness':['33'*32],'witness':'44'*32},'fee':1124})
        result=self.capture('print('+repr(payload)+'); print("raw_tx: '+ '55'*32+'")',
            {'kind':'logs','service':'watchmand'})
        row=json.loads(result['lines'][0])
        self.assertEqual('a'*64,row['txid']);self.assertEqual(1124,row['fee'])
        self.assertEqual('[redacted]',row['raw_tx']);self.assertEqual('[redacted]',row['signed_tx'])
        self.assertEqual('[redacted]',row['details']['txinwitness'])
        self.assertEqual('[redacted]',row['details']['witness'])
        self.assertEqual('[redacted sensitive text]',result['lines'][1])

    def test_public_witness_failure_reason_is_preserved(self):
        payload=json.dumps({'message':'recovery witness validation failed','waiting':1})
        result=self.capture('print('+repr(payload)+'); print("witness: '+'77'*32+'")',
            {'kind':'logs','service':'watchmand'})
        self.assertEqual('recovery witness validation failed',json.loads(result['lines'][0])['message'])
        self.assertEqual('[redacted sensitive text]',result['lines'][1])



if __name__ == '__main__':
    unittest.main()
