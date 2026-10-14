"""Optional isolated PostgreSQL fixture; never connects to a deployment database.

Run with FINANCIAL_OBSERVER_TEST_POSTGRES_HOST=127.0.0.2 and
FINANCIAL_OBSERVER_TEST_FORK_SOURCE pointing at the read-only local fork. The
fixture replays committed migrations, creates a unique database and retains it
for inspection. Synthetic policy/signature bytes are deliberately NOT money or
cryptographic qualification: the observer only exports undecided source facts.
"""
import importlib.util
import json
import os
import re
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('postgres_financial_bridge',Path(__file__).resolve().parents[1]/'server-observer.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


@unittest.skipUnless(os.environ.get('FINANCIAL_OBSERVER_TEST_POSTGRES_HOST'),'explicit isolated test PostgreSQL not selected')
class FinancialPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host=os.environ['FINANCIAL_OBSERVER_TEST_POSTGRES_HOST']
        if host!='127.0.0.2':raise ValueError('only explicitly isolated local fixture cluster accepted')
        source=Path(os.environ['FINANCIAL_OBSERVER_TEST_FORK_SOURCE'])
        cls.head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        cls.database='financial_observer_'+uuid.uuid4().hex
        cls.command=['psql','-h',host,'-U','postgres','-X','-v','ON_ERROR_STOP=1','-At']
        subprocess.run(cls.command+['-d','postgres','-c','CREATE DATABASE '+cls.database],check=True,capture_output=True,text=True)
        files=subprocess.check_output(['git','-C',str(source),'ls-tree','--name-only',cls.head,'server/src/database/migrations/'],text=True).splitlines()
        files=[name for name in files if re.search(r'/V\d+__.*\.sql$',name)]
        files.sort(key=lambda name:int(re.search(r'/V(\d+)__',name)[1]))
        cls.migrations=len(files)
        for name in files:
            sql=subprocess.check_output(['git','-C',str(source),'show',cls.head+':'+name],text=True)
            cls.sql(sql)
        cls.sql("""
            INSERT INTO vtxo(vtxo_id,vtxo,expiry,created_at,updated_at,vtxo_txid,exit_delta,policy_type,policy,server_pubkey,amount,anchor_point,spend_state,oor_spent_txid,onchain_spent_txid) VALUES
            ('old:0',decode('00','hex'),1000,now(),now(),'old',12,'pubkey',decode('0002','hex'),'public',5000,'backing:0','spent','new',NULL),
            ('new:0',decode('00','hex'),1001,now(),now(),'new',12,'pubkey',decode('0003','hex'),'public',4999,'backing:0','unclaimed',NULL,NULL),
            ('padding:0',decode('00','hex'),1000,now(),now(),'padding',12,'pubkey',decode('0002dfa52f6690299d2d6a08323083e290597b56fee125063e5f4e2957731639c42c','hex'),'public',330,'backing:0','unclaimed',NULL,NULL),
            ('retired:0',decode('00','hex'),1000,now(),now(),'retired',12,'pubkey',decode('00','hex'),'public',777,'retired:0','spent',NULL,'retired-tx');
            INSERT INTO pending_board(id,vtxo_id,vtxo,expiry) VALUES('pending','pending:0',decode('00','hex'),1000);
            INSERT INTO fallback_record VALUES(decode(repeat('02',33),'hex'),decode('0014'||repeat('00',20),'hex'),decode(repeat('00',78),'hex'),decode(repeat('00',64),'hex'),decode(repeat('00',64),'hex'));
            INSERT INTO key_link VALUES(decode(repeat('03',33),'hex'),decode(repeat('02',33),'hex'),17);
            INSERT INTO vtxo_pool(expiry_height,amount,vtxo) VALUES(1000,12000,decode('00','hex'));
            INSERT INTO lightning_node(pubkey,payment_created_index,payment_updated_index,created_at,updated_at) VALUES(decode(repeat('02',33),'hex'),0,0,now(),now());
            INSERT INTO htlc_vtxo(id,payment_hash,htlc_expiry,direction) SELECT id,'public-payment-hash',900,'incoming' FROM vtxo WHERE vtxo_id='new:0';
            INSERT INTO htlc_settlement(payment_hash,preimage,created_at) VALUES('public-payment-hash','fixture-secret-never-exported',now());
            INSERT INTO round_participation(unlock_hash,unlock_preimage,created_at,scheduled_height) VALUES('public-hash','fixture-unlock-never-exported',now(),999);
            INSERT INTO round_part_input(participation_id,vtxo_id) SELECT id,'old:0' FROM round_participation;
            INSERT INTO round_part_output(participation_id,policy,amount) SELECT id,decode('0003','hex'),4999 FROM round_participation;
        """)

    @classmethod
    def sql(cls,sql):
        return subprocess.run(cls.command+['-d',cls.database],input=sql,check=True,capture_output=True,text=True).stdout

    def test_actual_committed_schema_retains_candidates_without_exposing_secrets(self):
        def operation(kind,sql):
            self.assertEqual(kind,'sql')
            return self.sql(sql)
        with patch.object(bridge,'ops',side_effect=operation):result=bridge.claimant_inventory()
        self.assertEqual({'old:0','new:0','padding:0'},{row['vtxo_id'] for row in result['rows']['coins']})
        self.assertEqual(17,result['rows']['links'][0]['child_index'])
        self.assertTrue(result['rows']['htlcs'][0]['collection_recorded'])
        self.assertTrue(result['rows']['participations'][0]['unlock_available'])
        self.assertEqual('old:0',result['rows']['participation_inputs'][0]['vtxo_id'])
        self.assertEqual(4999,result['rows']['participation_outputs'][0]['amount'])
        self.assertNotIn('fixture-secret-never-exported',json.dumps(result))
        self.assertNotIn('fixture-unlock-never-exported',json.dumps(result))
        self.assertTrue(result['candidate_tables_complete']);self.assertFalse(result['complete'])
        evidence=os.environ.get('FINANCIAL_OBSERVER_TEST_EVIDENCE')
        if evidence:
            path=Path(evidence);path.mkdir(parents=True,exist_ok=True)
            (path/'postgres-result.json').write_text(json.dumps({'result':'PASS','source_commit':self.head,'migrations':self.migrations,'retained_fixture_database':self.database,'scope':'actual committed PostgreSQL schema read-only candidate query with synthetic fixture bytes; no cryptographic or money qualification','observation':result},indent=2)+'\n')


if __name__=='__main__':unittest.main()
