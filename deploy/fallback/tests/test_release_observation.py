import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('release_observation',Path(__file__).resolve().parents[1]/'release-observation.py')
receipt=importlib.util.module_from_spec(spec);spec.loader.exec_module(receipt)

class ReleaseReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.deploy=self.root/'deploy';self.prep=self.root/'prep';self.witness=self.root/'volume/expiry-payout-witness'
        for p in [self.deploy/'state/signet-fallback',self.prep/'freeze-ordinary-2',self.witness]:p.mkdir(parents=True)
        self.sha='a'*64;self.head='b'*40;self.txid='c'*64
        self.row={'txid':self.txid,'kind':'expiry-payout','raw_hex':'00','settlements':[{'id':'coin:0','spk_hex':'0014','fee_sat':7}]}
        self.db={'database':'bark-server-db','cluster_system_identifier':'123','nursery_count':1,'rows':[copy.deepcopy(self.row)]}
        self.files={'captaind':{'sha256':self.sha},'watchmand':{'sha256':self.sha},'bark':{'sha256':self.sha},'barkd':{'sha256':self.sha}}
        (self.prep/'qualified-binaries.json').write_text(json.dumps({'binaries':self.files}))
        (self.prep/'freeze-ordinary-2/frozen-nursery.json').write_text(json.dumps([self.row]))
        (self.deploy/'state/signet-fallback/captaind.toml').write_text('network="signet"\nvtxo_lifetime=48\n[vtxopool]\nvtxo_lifetime=48\n[expiry_payout]\nenabled=true\nmin_payout_sat=1000\n')
        (self.witness/'identity.json').write_text(json.dumps({'version':1,'genesis':'public-genesis','server_pubkey':'public-key'}))
        (self.witness/'history').write_text(self.txid+'\n')
        (self.witness/(self.txid+'.json')).write_text(json.dumps({'tx':[0],'settlements':[{'id':'coin:0','spk':[0,20],'fee_sat':7}]}))
        self.state={'Id':'container','Image':'image','Path':'captaind','Args':['--config','/config/captaind.toml','start'],
                    'State':{'Pid':9,'StartedAt':'start','Running':True,'Health':{'Status':'healthy'}},'RestartCount':0,
                    'Mounts':[{'Destination':'/config/captaind.toml','Source':str(self.deploy/'state/signet-fallback/captaind.toml'),'RW':False},
                              {'Destination':'/data/captaind','Source':str(self.root/'volume')}]}
    def run_command(self,*args):
        if args[0].endswith('ops.sh'):
            self.assertEqual('sql',args[1]);self.assertIn('REPEATABLE READ READ ONLY',args[2]);self.assertIn("statement_timeout='8s'",args[2])
            return 'BEGIN\nSET\n'+json.dumps(self.db)+'\nCOMMIT\n'
        self.assertEqual('docker',args[0]);self.assertEqual('exec',args[1])
        if args[-1]=='--version':return 'binary ('+self.head+')'
        if args[3]=='sh':return '/usr/local/bin/'+args[-1]
        self.assertEqual('sha256sum',args[3]);return self.sha+'  executable'
    def observe(self):
        with patch.object(receipt,'DEPLOY',self.deploy),patch.object(receipt,'PREP',self.prep),patch.object(receipt,'container',return_value=self.state),patch.object(receipt,'run',side_effect=self.run_command):
            return receipt.observe()
    def test_valid_receipt_binds_current_settlements_and_running_process_without_private_payloads(self):
        r=self.observe();self.assertTrue(r['preservation']['inventory_consistent'])
        self.assertEqual([],r['preservation']['mismatches']);self.assertEqual([],r['guard']['current_witness_database_mismatches'])
        self.assertTrue(r['guard']['startup_validation_completed']);self.assertEqual(48,r['settings']['pool_lifetime'])
        self.assertNotIn('raw_hex',json.dumps(r));self.assertNotIn('"tx":',json.dumps(r));self.assertFalse(r['complete'])
    def test_same_txid_with_redirected_destination_or_fee_does_not_preserve_accounting(self):
        for field,value in [('spk_hex','0015'),('fee_sat',8)]:
            self.db['rows'][0]=copy.deepcopy(self.row);self.db['rows'][0]['settlements'][0][field]=value
            with self.subTest(field=field):
                r=self.observe();self.assertEqual([self.txid],r['preservation']['mismatches'])
                self.assertEqual([self.txid],r['guard']['current_witness_database_mismatches'])
    def test_missing_transaction_is_not_a_preservation_pass(self):
        self.db['rows']=[];r=self.observe()
        self.assertEqual([self.txid],r['preservation']['mismatches']);self.assertEqual([],r['preservation']['current_identity_rows'])
    def test_replaced_running_executable_fails_closed(self):
        original=self.run_command
        def changed(*args):return 'd'*64+'  executable' if args[-1]=='/proc/1/exe' else original(*args)
        with patch.object(self,'run_command',side_effect=changed),self.assertRaises(ValueError):self.observe()
    def test_incomplete_witness_history_is_not_startup_evidence(self):
        (self.witness/'history').write_text(self.txid)
        with self.assertRaises(ValueError):self.observe()

if __name__=='__main__':unittest.main()
