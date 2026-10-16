import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
import subprocess
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
    def test_mixed_urgent_reservations_charge_once_and_bindings_stay_private(self):
        private_package=[[1]*10,[2]*10]
        binding='a'*64
        record=b'1000\nu:1:2000\np:1:'+binding.encode()+b':'+encode(private_package)+b'\n500\nu:2:3000\n'
        facts=reader.project(source(ledger=record))
        self.assertEqual(facts['total_charged_sat'],32364)
        self.assertEqual(facts['reserved_ceiling_sum_sat'],6500)
        self.assertEqual(facts['attempt_count'],4)
        self.assertEqual(facts['ordinary_attempt_count'],2)
        self.assertEqual(facts['urgent_attempt_count'],2)
        self.assertEqual(facts['urgent_reserved_ceiling_sum_sat'],5000)
        self.assertEqual(facts['urgent_binding_count'],1)
        self.assertEqual(facts['remaining_urgent_allowance_sat'],10000)
        self.assertEqual(facts['remaining_discretionary_allowance_sat'],17636)
        encoded=encode(facts)
        self.assertNotIn(binding.encode(),encoded)
        self.assertNotIn(encode(private_package),encoded)
        self.assertFalse(facts['qualified'])

    def test_urgent_binding_never_replenishes_or_charges_again(self):
        reservation=b'u:1:15000\n';binding=b'p:1:'+b'a'*64+b':'+encode([[1]*10])+b'\n'
        first=reader.project(source(ledger=reservation))
        bound=reader.project(source(ledger=reservation+binding))
        for key in ('total_charged_sat','reserved_ceiling_sum_sat','remaining_urgent_allowance_sat','remaining_ordinary_allowance_sat'):
            self.assertEqual(first[key],bound[key])
        self.assertEqual(bound['remaining_urgent_allowance_sat'],0)
        self.assertEqual(bound['total_charged_sat'],40864)
        self.assertEqual(bound['urgent_binding_count'],1)

    def test_urgent_indices_amounts_and_shared_or_urgent_exhaustion_refuse(self):
        for raw in (b'u:0:1\n',b'u:01:1\n',b'u:2:1\n',b'u:1:0\n',b'u:1:01\n',
                    b'u:1:-1\n',b'u:1:1\nu:1:1\n',b'u:1:15001\n',
                    b'34136\nu:1:1\n',b'u:1:1',b'unknown:1\n'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):reader.project(source(ledger=raw))

    def test_bindings_require_prior_reservation_exact_once_and_canonical_envelope(self):
        good=b'p:1:'+b'a'*64+b':'+encode([[1]*10])+b'\n'
        for raw in (good,b'u:1:1\n'+good+good,b'u:1:1\n'+good.replace(b'p:1:',b'p:01:'),
                    b'u:1:1\n'+good.replace(b'a'*64,b'A'*64),
                    b'u:1:1\n'+good.replace(b'a'*64,b'0'*64),
                    b'u:1:1\n'+good.replace(b'[[1,',b'[[ 1,')):
            with self.subTest(raw=raw),self.assertRaises(ValueError):reader.project(source(ledger=raw))

    def test_binding_byte_envelope_bounds_and_types_refuse(self):
        for package in ([],[[1]*10]*3,[[1]*9],[[256]*10],[[-1]*10],[[True]*10],
                        [['1']*10],{'secret':'never-export'},[[1]*10,[1]*10],[[[1]]*10]):
            raw=b'u:1:1\np:1:'+b'a'*64+b':'+encode(package)+b'\n'
            with self.subTest(package=package),self.assertRaises(ValueError):reader.project(source(ledger=raw))

    def test_historical_full_allowance_is_observed_without_claiming_new_urgent_isolation(self):
        facts=reader.project(source(ledger=b'34136\n'))
        self.assertEqual(facts['remaining_ordinary_allowance_sat'],0)
        self.assertEqual(facts['remaining_discretionary_allowance_sat'],0)
        self.assertEqual(facts['remaining_urgent_allowance_sat'],15000)
        self.assertFalse(facts['urgent_selection_isolation_verified'])

    def test_crlf_mixed_history_matches_native_lines(self):
        self.assertEqual(reader.project(source(ledger=b'1000\r\nu:1:2000\r\n'))['total_charged_sat'],28864)

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
    def test_fixed_root_helper_accepts_actual_owned_bind_and_named_volume(self):
        for mount in ({'Destination':'/data/captaind','Type':'bind','Source':str(reader.OWNED_PARENT/'artifacts/existing/data/captaind'),'RW':True},
                      {'Destination':'/data/captaind','Type':'volume','Name':'abandon-signet-fallback_captaind','Source':'/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data','RW':True}):
            raw=json.dumps({'container_id':'a'*64,'mounts':[mount]}).encode()
            with patch.object(reader.subprocess,'run',return_value=subprocess.CompletedProcess([],0,raw))as command:
                directory,binding=reader.source_mount()
            self.assertEqual(str(directory),mount['Source']+'/direct-expiry-witness')
            self.assertEqual(command.call_args.args[0][:3],['/usr/bin/docker','--host','unix:///var/run/docker.sock'])
            self.assertNotIn('DOCKER_HOST',command.call_args.kwargs['env'])
    def test_wrong_missing_duplicate_or_shadow_mount_refuses(self):
        good={'Destination':'/data/captaind','Type':'bind','Source':str(reader.OWNED_PARENT/'artifacts/existing/data/captaind'),'RW':True}
        for mounts in ([],[good,good],[{**good,'RW':False}],[{**good,'Source':'/etc/captaind'}],
                       [good,{'Destination':'/data/captaind/direct-expiry-witness','Type':'bind','Source':'/elsewhere','RW':True}],
                       [{**good,'Source':str(reader.OWNED_PARENT/'../other/captaind')}],
                       [{**good,'Type':'volume','Name':'other'}]):
            raw=json.dumps({'container_id':'a'*64,'mounts':mounts}).encode()
            with patch.object(reader.subprocess,'run',return_value=subprocess.CompletedProcess([],0,raw)),self.assertRaises(ValueError):reader.source_mount()
    def test_bridge_fixed_projection_never_claims_urgent_readiness(self):
        facts=reader.project(source());facts.update(observed_at=1.0,completed_at=2.0,source_mount_identity_sha256="5"*64)
        mounts=[{'Destination':'/data/captaind','Type':'volume','Name':'abandon-signet-fallback_captaind',
            'Source':'/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data','RW':True}]
        with patch.object(bridge,'run',return_value=json.dumps(facts))as operation:
            result=bridge.native_ordinary_fee_budget()
        self.assertEqual(operation.call_args.args[0],['sudo','-n','/usr/bin/python3','-I','/opt/abandon-simulation/native-budget-observation.py'])
        self.assertIsNone(result['whole_input_urgent_eligible_cash_sat']);self.assertFalse(result['qualified'])
    def test_bridge_never_returns_added_private_fields_or_true_qualification(self):
        mounts=[{'Destination':'/data/captaind','Type':'volume','Name':'abandon-signet-fallback_captaind',
            'Source':'/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data','RW':True}]
        for alteration in ({'private_history':'redacted'},{'qualified':True}):
            facts=reader.project(source());facts.update(observed_at=1.0,completed_at=2.0,source_mount_identity_sha256="5"*64);facts.update(alteration)
            with patch.object(bridge,'run',return_value=json.dumps(facts)),self.assertRaises(ValueError):bridge.native_ordinary_fee_budget()

if __name__=='__main__':unittest.main()
