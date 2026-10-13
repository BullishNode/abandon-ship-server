import json
import os
import subprocess
import unittest
from pathlib import Path


class ServerImagePinTest(unittest.TestCase):
    def test_real_compose_render_preserves_clients_and_default_compatibility(self):
        deploy = Path(__file__).resolve().parents[2]
        for server in [None, '', '7596-public-fixture']:
            env = {**os.environ, 'FORK_COMMIT': '8f-public-fixture',
                   'PG_PASS': 'fixture-not-a-credential', 'WEB_TAG': 'fixture', 'WEB_API_TAG': 'fixture'}
            env.pop('SERVER_FORK_COMMIT', None)
            if server is not None:
                env['SERVER_FORK_COMMIT'] = server
            result = subprocess.run(['docker', 'compose', '--project-directory', str(deploy),
                                     '-f', str(deploy / 'fallback/compose.yaml'),
                                     '--profile', 'cli', 'config', '--format', 'json'],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, check=True)
            services = json.loads(result.stdout)['services']
            with self.subTest(server=server):
                expected = 'abandon-ship/captaind:fallback-' + (server or '8f-public-fixture')
                self.assertEqual(expected, services['fb-captaind']['image'])
                self.assertEqual(expected, services['fb-watchmand']['image'])
                for client in ['fb-barkd', 'fb-barkd2', 'fb-cli']:
                    self.assertEqual('abandon-ship/bark:fallback-8f-public-fixture', services[client]['image'])


if __name__ == '__main__':
    unittest.main()
