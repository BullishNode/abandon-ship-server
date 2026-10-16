import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
reader=module('native_budget_reader',ROOT/'native-budget-observation.py')
bridge=module('native_budget_bridge',ROOT/'server-observer.py')

def encode(value):return json.dumps(value,separators=(',',':')).encode()
def source(adopt=True,ledger=b''):
    opening={'version':1,'network':'signet','genesis':'1'*64,'server_pubkey':'02'+'2'*64,
        'history_prefix_bytes':65 if adopt else 0,
        'history_prefix_sha256':'3'*64 if adopt else hashlib.sha256(b'').hexdigest(),
        'native_spent_sat':4985 if adopt else 0,'external_spent_sat':879 if adopt else 0,
        'accounting_receipt_sha256':'4'*64 if adopt else None}
    raw=encode(opening);digest=hashlib.sha256(raw).hexdigest()
    policy={'urgent_reserve_sat':15000,'ordinary_budget_sat':60000,'external_future_reserved_sat':20000,
        'opening_sha256':digest if adopt else None}
    return {'ordinary-fee-policy.json':encode({'policy':policy,'opening_sha256':digest}),
        'ordinary-fee-opening.json':raw,'ordinary-fee-budget':ledger}

def install(directory,files):
    for name,raw in files.items():(directory/name).write_bytes(raw)

class NativeBudgetObservation(unittest.TestCase):
    def test_adopted_costs_partition_and_full_ceiling_charges_are_distinct(self):
        facts=reader.project(source(ledger=b'10000\n5000\n'))
        self.assertEqual(facts['opening_charge_sat'],25864)
        self.assertEqual(facts['total_charged_sat'],40864)
        self.assertEqual(facts['remaining_ordinary_allowance_sat'],19136)
        self.assertEqual(facts['attempt_count'],2)
        self.assertEqual(facts['opening_native_spent_sat']+facts['opening_external_spent_sat'],5864)
        self.assertFalse(facts['qualified']);self.assertFalse(facts['runtime_loaded_identity_verified'])
    def test_fresh_history_has_no_adopted_costs(self):
        self.assertEqual(reader.project(source(False))['opening_charge_sat'],20000)
        files=source(False);opening=json.loads(files['ordinary-fee-opening.json']);opening['native_spent_sat']=1
        files['ordinary-fee-opening.json']=encode(opening)
        with self.assertRaises(ValueError):reader.project(files)
    def test_digest_mismatch_cannot_be_adopted(self):
        files=source();files['ordinary-fee-opening.json']=files['ordinary-fee-opening.json'].replace(b'4985',b'4986')
        with self.assertRaises(ValueError):reader.project(files)
    def test_torn_negative_zero_noncanonical_overflow_and_overbudget_refuse(self):
        for raw in (b'1',b'-1\n',b'0\n',b'01\n',b'1\x0b2\n',b'\n',b'2100000000000001\n',b'34137\n'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):reader.project(source(ledger=raw))
    def test_exact_exhaustion_never_replenishes(self):
        self.assertEqual(reader.project(source(ledger=b'34136\n'))['remaining_ordinary_allowance_sat'],0)
        with self.assertRaises(ValueError):reader.project(source(ledger=b'34136\n1\n'))
    def test_unknown_private_fields_and_noncanonical_bytes_refuse(self):
        files=source();stored=json.loads(files['ordinary-fee-policy.json']);stored['private_history']='never-export'
        files['ordinary-fee-policy.json']=encode(stored)
        with self.assertRaises(ValueError):reader.project(files)
        files=source();files['ordinary-fee-opening.json']+=b'\n'
        with self.assertRaises(ValueError):reader.project(files)
    def test_missing_extra_and_oversized_inputs_refuse(self):
        for files in ({},dict(source(),extra=b'private'),dict(source(),**{'ordinary-fee-budget':b'1'*(2*1024*1024+1)})):
            with self.assertRaises(ValueError):reader.project(files)
    def test_regular_stable_owned_files_read(self):
        with tempfile.TemporaryDirectory()as temporary:
            path=Path(temporary);install(path,source())
            self.assertEqual(reader.read_directory(path)['total_charged_sat'],25864)
    def test_symlink_file_and_directory_refuse(self):
        with tempfile.TemporaryDirectory()as temporary:
            path=Path(temporary);actual=path/'actual';actual.mkdir();install(actual,source());(path/'alias').symlink_to(actual)
            with self.assertRaises(OSError):reader.read_directory(path/'alias')
            (actual/'ordinary-fee-budget').unlink();(actual/'ordinary-fee-budget').symlink_to(actual/'ordinary-fee-opening.json')
            with self.assertRaises(OSError):reader.read_directory(actual)
    def test_fifo_cannot_block_reader(self):
        with tempfile.TemporaryDirectory()as temporary:
            path=Path(temporary);install(path,source());(path/'ordinary-fee-budget').unlink();os.mkfifo(path/'ordinary-fee-budget')
            with self.assertRaises(ValueError):reader.read_directory(path)
    def test_same_size_mutation_after_read_refuses(self):
        original=reader.project
        with tempfile.TemporaryDirectory()as temporary:
            path=Path(temporary);install(path,source())
            def mutate(files):
                result=original(files);p=path/'ordinary-fee-opening.json';p.write_bytes(p.read_bytes().replace(b'4985',b'4986'));return result
            with patch.object(reader,'project',side_effect=mutate),self.assertRaises(ValueError):reader.read_directory(path)
    def test_appending_after_read_refuses(self):
        original=reader.project
        with tempfile.TemporaryDirectory()as temporary:
            path=Path(temporary);install(path,source())
            def mutate(files):
                result=original(files)
                with (path/'ordinary-fee-budget').open('ab')as stream:stream.write(b'1\n')
                return result
            with patch.object(reader,'project',side_effect=mutate),self.assertRaises(ValueError):reader.read_directory(path)
    def test_operation_has_no_caller_paths_or_arguments(self):
        self.assertEqual(bridge.request('{"kind":"native_ordinary_fee_budget"}'),('native_ordinary_fee_budget',None))
        with self.assertRaises(ValueError):bridge.request('{"kind":"native_ordinary_fee_budget","path":"/elsewhere"}')
    def test_bridge_rejects_wrong_mount_without_helper_call(self):
        with patch.object(bridge,'run',return_value='[]')as operation,self.assertRaises(ValueError):bridge.native_ordinary_fee_budget()
        self.assertEqual(operation.call_count,1)
    def test_bridge_fixed_projection_never_claims_urgent_readiness(self):
        facts=reader.project(source());facts.update(observed_at=1.0,completed_at=2.0)
        mounts=[{'Destination':'/data/captaind','Type':'volume','Name':'abandon-signet-fallback_captaind',
            'Source':'/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data','RW':True}]
        with patch.object(bridge,'run',side_effect=[json.dumps(mounts),json.dumps(facts)])as operation:
            result=bridge.native_ordinary_fee_budget()
        self.assertEqual(operation.call_args.args[0],['sudo','-n','/usr/bin/python3','-I','/opt/abandon-simulation/native-budget-observation.py'])
        self.assertIsNone(result['whole_input_urgent_eligible_cash_sat']);self.assertFalse(result['qualified'])
    def test_bridge_never_returns_added_private_fields_or_true_qualification(self):
        mounts=[{'Destination':'/data/captaind','Type':'volume','Name':'abandon-signet-fallback_captaind',
            'Source':'/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data','RW':True}]
        for alteration in ({'private_history':'redacted'},{'qualified':True}):
            facts=reader.project(source());facts.update(observed_at=1.0,completed_at=2.0);facts.update(alteration)
            with patch.object(bridge,'run',side_effect=[json.dumps(mounts),json.dumps(facts)]),self.assertRaises(ValueError):bridge.native_ordinary_fee_budget()

if __name__=='__main__':unittest.main()
