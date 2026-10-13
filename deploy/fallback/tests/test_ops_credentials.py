import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class CredentialTransportTests(unittest.TestCase):
    def test_rpc_password_uses_stdin_and_never_an_argument(self):
        source = Path(__file__).parents[1] / 'ops.sh'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deploy = root / 'fallback-deploy'
            (deploy / 'fallback').mkdir(parents=True)
            (deploy / 'fallback/ops.sh').write_bytes(source.read_bytes())
            original = root / 'original'
            (original / 'state/signet').mkdir(parents=True)
            password = 'fixture-not-a-real-credential'
            (original / 'state/signet/secrets.env').write_text(
                'RPC_USER=fixture\nRPC_PASS=' + password + '\n')
            binaries = root / 'bin'
            binaries.mkdir()
            sudo = binaries / 'sudo'
            sudo.write_text('#!/usr/bin/python3\nimport sys,json\n'
                            'print(json.dumps({"argv":sys.argv[1:],"stdin":sys.stdin.read()}))\n')
            sudo.chmod(0o755)
            result = subprocess.run(['bash', str(deploy / 'fallback/ops.sh'), 'cli',
                                     'getblockhash', '0'], capture_output=True, check=True,
                                    env={**os.environ, 'PATH': str(binaries) + ':' + os.environ['PATH'],
                                         'SIGNET_DEPLOY': str(original)})
            captured = json.loads(result.stdout)
            self.assertEqual(password + '\n', captured['stdin'])
            self.assertIn('-stdinrpcpass', captured['argv'])
            self.assertEqual(['getblockhash', '0'], captured['argv'][-2:])
            self.assertFalse(any(password in arg or arg.startswith('-rpcpassword=')
                                 for arg in captured['argv']))
