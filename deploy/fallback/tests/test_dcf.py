"""Shell CLI contract with a mock Docker executable; no Docker or live state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class ActiveComposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dcf-cli-')
        self.root = Path(self.temp.name)
        self.deploy = self.root / 'project with spaces' / 'deploy'
        self.fallback = self.deploy / 'fallback'
        self.fallback.mkdir(parents=True)
        self.state = self.deploy / 'state' / 'signet-fallback'
        self.state.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / 'dcf'
        shutil.copyfile(source, self.fallback / 'dcf')
        self.mock_bin = self.root / 'bin'
        self.mock_bin.mkdir()
        self.capture = self.root / 'docker-arguments.json'
        docker = self.mock_bin / 'docker'
        docker.write_text(f'#!{sys.executable}\n'
                          'import json,os,sys\n'
                          'from pathlib import Path\n'
                          'Path(os.environ["MOCK_DOCKER_CAPTURE"]).write_text('
                          'json.dumps({"cwd":os.getcwd(),"args":sys.argv[1:]}))\n')
        docker.chmod(0o700)
        self.env = {**os.environ, 'PATH': str(self.mock_bin) + os.pathsep + os.environ['PATH'],
                    'MOCK_DOCKER_CAPTURE': str(self.capture)}

    def tearDown(self):
        self.temp.cleanup()

    def run_dcf(self, *args):
        return subprocess.run(['/bin/sh', str(self.fallback / 'dcf'), *args],
                              cwd=self.root, env=self.env, text=True, capture_output=True, timeout=5)

    def secrets(self):
        # Empty synthetic file; no real secret or configuration is read.
        path = self.state / 'secrets.env'
        path.touch(mode=0o600)

    def active(self):
        (self.state / 'active.compose.yaml').write_text('services: {}\n')

    def test_fixed_active_override_follows_base_before_unchanged_compose_arguments(self):
        self.secrets()
        self.active()
        result = self.run_dcf('ps', '--format', '{{.Name}} {{.State}}')
        self.assertEqual(0, result.returncode, result.stderr)
        observed = json.loads(self.capture.read_text())
        self.assertEqual(str(self.deploy), observed['cwd'])
        self.assertEqual(['compose', '-p', 'abandon-signet-fallback', '--project-directory', '.',
                          '--env-file', 'fallback/signet-fallback.env',
                          '--env-file', 'state/signet-fallback/secrets.env',
                          '-f', 'fallback/compose.yaml', '-f', 'state/signet-fallback/active.compose.yaml',
                          'ps', '--format', '{{.Name}} {{.State}}'], observed['args'])

    def test_missing_active_override_refuses_before_docker(self):
        self.secrets()
        result = self.run_dcf('up', '-d')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('active.compose.yaml', result.stderr)
        self.assertFalse(self.capture.exists())

    def test_existing_secrets_check_still_runs_first_and_never_calls_docker(self):
        result = self.run_dcf('restart')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('render.sh', result.stderr)
        self.assertNotIn('active.compose.yaml', result.stderr)
        self.assertFalse(self.capture.exists())
