#!/usr/bin/env python3
"""Capture one consistent live signet DB snapshot and restore it into an audit DB.

Never restore over the live database or start a captaind. Sensitive archives stay
on the target. This qualifies restore mechanics; frozen final-state audit and
independent witness reconciliation remain necessary.
"""
from pathlib import Path
import datetime,hashlib,json,os,re,subprocess,sys

ROOT=Path.home()/'abandon-signet-fallback/deploy'
SOURCE='bark-server-db'

def main():
    os.umask(0o077)
    assert len(sys.argv)==2
    out=Path(sys.argv[1]);out.mkdir(mode=0o700,parents=False,exist_ok=False)
    name='fallback_restore_audit_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
    assert re.fullmatch('fallback_restore_audit_[0-9]{14}',name)
    base=['sudo','./fallback/dcf','exec','-T','fb-postgres']
    psql=base+['psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres']
    def sql(db,text):
        return subprocess.check_output(psql+['-d',db],input=text,text=True,cwd=ROOT).strip()
    owner=subprocess.Popen(psql+['-d',SOURCE],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=ROOT)
    try:
        owner.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n');owner.stdin.flush()
        snapshot=owner.stdout.readline().strip()
        assert re.fullmatch('[0-9A-F]+-[0-9A-F]+-[0-9]+',snapshot),'snapshot export failed'
        tables=json.loads(sql(SOURCE,"SELECT COALESCE(jsonb_agg(jsonb_build_array(schemaname,tablename) ORDER BY schemaname,tablename),'[]'::jsonb) FROM pg_tables WHERE schemaname NOT LIKE 'pg_%' AND schemaname!='information_schema';"))
        archive=out/'snapshot.dump'
        with archive.open('xb') as stream:
            p=subprocess.run(base+['pg_dump','-U','postgres','-d',SOURCE,'--format=custom','--snapshot',snapshot],stdout=stream,stderr=subprocess.PIPE,cwd=ROOT)
            assert p.returncode==0,'snapshot dump failed'
        subprocess.run(base+['createdb','-U','postgres',name],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        with archive.open('rb') as stream:
            p=subprocess.run(base+['pg_restore','-U','postgres','--dbname',name,'--exit-on-error','--no-owner'],stdin=stream,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,cwd=ROOT)
            assert p.returncode==0,'isolated restore failed; private archive retained'
        restored=json.loads(sql(name,"SELECT COALESCE(jsonb_agg(jsonb_build_array(schemaname,tablename) ORDER BY schemaname,tablename),'[]'::jsonb) FROM pg_tables WHERE schemaname NOT LIKE 'pg_%' AND schemaname!='information_schema';"))
        assert tables==restored,'restored table inventory differs'
        checks=[]
        def data_digest(db,table,import_snapshot=False):
            ident='.'.join('"'+v.replace('"','""')+'"' for v in table)
            statement='BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; '
            if import_snapshot:statement+="SET TRANSACTION SNAPSHOT '"+snapshot+"'; "
            statement+='COPY (SELECT row_to_json(t)::text FROM '+ident+' t ORDER BY row_to_json(t)::text) TO STDOUT; ROLLBACK;\n'
            child=subprocess.Popen(psql+['-d',db],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT)
            child.stdin.write(statement.encode());child.stdin.close()
            digest=hashlib.sha256();rows=0
            for line in child.stdout:
                digest.update(line);rows+=1
            assert child.wait(timeout=60)==0,'table digest failed'
            return {'rows':rows,'sha256':digest.hexdigest()}
        for table in tables:
            before=data_digest(SOURCE,table,True);after=data_digest(name,table)
            check={'table':'.'.join(table),'snapshot':before,'restored':after,'matches':before==after}
            checks.append(check)
            (out/'table-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
            assert check['matches'],'restored data differs for '+check['table']
        def schema(db,import_snapshot=False):
            command=base+['pg_dump','-U','postgres','-d',db,'--schema-only','--no-owner','--no-acl']
            if import_snapshot:command+=['--snapshot',snapshot]
            return subprocess.check_output(command,cwd=ROOT)
        before_schema=schema(SOURCE,True);after_schema=schema(name)
        (out/'snapshot-schema.sql').write_bytes(before_schema)
        (out/'restored-schema.sql').write_bytes(after_schema)
        def canonical_schema(raw):
            # Recent PostgreSQL emits a fresh matching psql restriction nonce
            # for each dump. Validate that pair; compare every other byte.
            starts=re.findall(rb'^\\restrict ([A-Za-z0-9]+)$',raw,re.MULTILINE)
            ends=re.findall(rb'^\\unrestrict ([A-Za-z0-9]+)$',raw,re.MULTILINE)
            assert len(starts)==len(ends)<=1 and starts==ends,'invalid dump restriction pair'
            if not starts:return raw
            for directive in [b'restrict',b'unrestrict']:
                raw=raw.replace(b'\\'+directive+b' '+starts[0]+b'\n',b'\\'+directive+b' <validated-dump-nonce>\n',1)
            return raw
        normalized_before=canonical_schema(before_schema);normalized_after=canonical_schema(after_schema)
        assert normalized_before==normalized_after,'restored schema differs after validated dump-nonce normalization'
        result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'result':'PASS_CONSISTENT_SNAPSHOT_ISOLATED_RESTORE',
          'source_database':SOURCE,'audit_database':name,'tables':len(checks),'rows':sum(x['snapshot']['rows'] for x in checks),
          'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'schema_sha256':hashlib.sha256(normalized_before).hexdigest(),'schema_normalization':'only one internally matching pg_dump restrict/unrestrict nonce; all other bytes identical',
          'live_database_restored':False,'captaind_started':False,'keys_and_archives':'remain private on target',
          'scope':'consistent live snapshot data and schema restored exactly; sequences under live writes and frozen final-state/witness qualification remain outstanding'}
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        if owner.poll() is None:
            try:owner.stdin.write('ROLLBACK;\n\\q\n');owner.stdin.flush();owner.stdin.close();owner.wait(timeout=20)
            except (OSError,subprocess.TimeoutExpired):owner.terminate();owner.wait(timeout=10)

if __name__=='__main__':main()
