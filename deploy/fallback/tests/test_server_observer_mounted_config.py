"""Fake Docker mounts/local files only; no Docker, deployment or VM actions."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('mounted_config_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


class MountedConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.parent=Path(self.tmp.name)
        self.deploy=self.parent/'deploy';self.deploy.mkdir()
        self.configs=self.parent/'artifacts/native/signet-20261015/configs';self.configs.mkdir(parents=True)
        for service in ('captaind','watchmand'):
            (self.configs/f'{service}.toml').write_text('network="signet"\n[fee_estimator]\nmax_fee_rate="2sat/vb"\n[bitcoind]\nrpc_pass="private-test-value"\n')
        self.deployment=patch.object(bridge,'DEPLOY',self.deploy);self.deployment.start()

    def tearDown(self):
        self.deployment.stop();self.tmp.cleanup()

    def mount(self,service='captaind',**changes):
        return {'Type':'bind','RW':False,'Destination':f'/config/{service}.toml',
                'Source':str(self.configs/f'{service}.toml'),**changes}

    def read(self,mounts,service='captaind'):
        with patch.object(bridge,'run',return_value=json.dumps(mounts)) as command:
            result=bridge.disk_financial_config(service)
        container=next(row[1] for row in bridge.WALLET_SOURCES.values() if row[0]==service)
        self.assertEqual(['sudo','-n','docker','inspect','--format','{{json .Mounts}}',container],command.call_args.args[0])
        return result

    def test_actual_mount_wins_over_stale_state_and_never_claims_loaded(self):
        stale=self.deploy/'state/signet-fallback';stale.mkdir(parents=True)
        (stale/'captaind.toml').write_text('network="signet"\ncln_array=["private-stale-ln-endpoint"]\n[fee_estimator]\nmax_fee_rate="9sat/vb"\n')
        result=self.read([self.mount()])
        self.assertEqual(2000,result['estimator_max_fee_rate_sat_kvb'])
        self.assertFalse(result['lightning_configured'])
        self.assertFalse(result['runtime_loaded_identity_verified'])
        self.assertEqual(str(self.configs/'captaind.toml'),result['source']['mount_source'])
        self.assertNotIn('private-test-value',json.dumps(result))
        self.assertNotIn('rpc_pass',json.dumps(result))
        self.assertEqual(2000,self.read([self.mount('watchmand')],'watchmand')['estimator_max_fee_rate_sat_kvb'])

    def test_missing_writable_duplicate_nonbind_or_wrong_destination_mount_refuses(self):
        for mounts in ([],[self.mount(RW=True)],[self.mount(),self.mount()],
                [self.mount(Type='volume')],[self.mount(Destination='/config/watchmand.toml')],{},[None]):
            with self.subTest(mounts=mounts),self.assertRaises(ValueError):self.read(mounts)

    def test_outside_relative_traversal_and_noncanonical_sources_refuse(self):
        for source in ('/etc/passwd','configs/captaind.toml',str(self.parent/'../outside.toml'),
                str(self.parent)+'//artifacts/native/signet-20261015/configs/captaind.toml',str(self.parent)):
            with self.subTest(source=source),self.assertRaises(ValueError):self.read([self.mount(Source=source)])

    def test_file_and_directory_symlinks_refuse_without_secret_projection(self):
        link=self.configs/'link.toml';link.symlink_to(self.configs/'captaind.toml')
        directory=self.parent/'linked-configs';directory.symlink_to(self.configs,target_is_directory=True)
        for source in (link,directory/'captaind.toml'):
            with self.subTest(source=source),self.assertRaises(OSError):self.read([self.mount(Source=str(source))])

    def test_large_or_nonregular_file_and_mount_inventory_refuse(self):
        large=self.configs/'large.toml';large.write_bytes(b'x'*65537)
        fifo=self.configs/'pipe.toml';os.mkfifo(fifo)
        for source in (large,fifo):
            with self.subTest(source=source),self.assertRaises(ValueError):self.read([self.mount(Source=str(source))])
        with self.assertRaises(ValueError):self.read([self.mount()]*33)

    def test_unknown_service_never_inspects_or_reads(self):
        with patch.object(bridge,'run') as command,self.assertRaises(ValueError):
            bridge.disk_financial_config('../captaind')
        command.assert_not_called()

    def test_snapshot_uses_same_actual_projection_and_preserves_incomplete_scope(self):
        chain={'chain':'signet','bestblockhash':'a'*64,'blocks':100,'initialblockdownload':False}
        def command(argv,**kwargs):
            return json.dumps([self.mount()]) if argv[-2]=='{{json .Mounts}}' else '{"status":"running"}'
        with patch.object(bridge,'run',side_effect=command),patch.object(bridge,'ops',side_effect=[json.dumps(chain),json.dumps(chain),'b'*64]):
            result=bridge.collect('snapshot','')
        self.assertFalse(result['lightning_configured']);self.assertFalse(result['complete'])
        self.assertFalse(result['disk_config']['runtime_loaded_identity_verified'])
        self.assertIsNone(result['wallet'])


if __name__=='__main__':unittest.main()
