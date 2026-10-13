#!/usr/bin/env python3
"""Fixed, bounded read-only receipt for the owned 8f signet upgrade.

Called by the existing observer as ubuntu, using its existing sudo permission.
No arguments, credentials, raw witness contents or wallet operations are exposed.
The frozen baseline and startup qualification are operator evidence, not a new
independent assurance about an untrusted host or a destructive restore test.
"""
import datetime,hashlib,json,re,subprocess,sys,tomllib
from pathlib import Path

DEPLOY=Path('/home/ubuntu/abandon-signet-fallback/deploy')
PREP=DEPLOY.parent/'release-prep/8f2286ee7-1'
MAX_BYTES=2*1024*1024

def digest(data):return hashlib.sha256(data).hexdigest()
def read(path):
    if path.stat().st_size>8*1024*1024:raise ValueError('file bound exceeded')
    return path.read_bytes()
def run(*args):
    p=subprocess.run(args,cwd=DEPLOY,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=12,check=True)
    if len(p.stdout)>MAX_BYTES:raise ValueError('command bound exceeded')
    return p.stdout.decode()
def container(service):
    row=json.loads(run('docker','inspect','abandon-signet-fallback-'+service+'-1'))[0]
    return row
def public_state(row):
    return {'id':row['Id'],'image':row['Image'],'pid':row['State']['Pid'],
            'started_at':row['State']['StartedAt'],'running':row['State']['Running'],
            'health':row['State'].get('Health',{}).get('Status'),'restarts':row['RestartCount']}
def parse_sql(value):
    rows=[json.loads(line) for line in value.splitlines() if line.startswith('{')]
    if len(rows)!=1:raise ValueError('database response invalid')
    return rows[0]
def settlement_identity(rows):
    return sorted([{'id':s['id'],'spk_hex':s['spk_hex'],'fee_sat':s['fee_sat']} for s in rows],key=lambda s:s['id'])
def public_transaction(row):
    return {'txid':row['txid'],'kind':row['kind'],'raw_sha256':digest(bytes.fromhex(row['raw_hex'])),
            'settlements':settlement_identity(row['settlements'])}

def observe():
    qualified=json.loads(read(PREP/'qualified-binaries.json'))
    baseline_bytes=read(PREP/'freeze-ordinary-2/frozen-nursery.json')
    baseline=json.loads(baseline_bytes)
    if not 1<=len(baseline)<=512:raise ValueError('baseline bound')
    ids=[r['txid'] for r in baseline]
    if len(set(ids))!=len(ids) or any(not re.fullmatch('[0-9a-f]{64}',i) for i in ids):raise ValueError('invalid baseline')
    cfg_path=DEPLOY/'state/signet-fallback/captaind.toml'
    cfg_bytes=read(cfg_path);cfg=tomllib.loads(cfg_bytes.decode())
    before={s:container(s) for s in ['fb-captaind','fb-watchmand','fb-barkd','fb-barkd2']}
    cap=before['fb-captaind']
    mounts={m['Destination']:m for m in cap['Mounts']}
    if mounts['/config/captaind.toml']['Source']!=str(cfg_path) or mounts['/config/captaind.toml']['RW']:raise ValueError('configuration mount differs')
    if cap['Path']!='captaind' or cap['Args']!=['--config','/config/captaind.toml','start']:raise ValueError('unexpected server process')
    binaries={}
    for name,service in [('captaind','fb-captaind'),('watchmand','fb-watchmand'),('bark','fb-barkd'),('barkd','fb-barkd'),('barkd2','fb-barkd2')]:
        binary='barkd' if name=='barkd2' else name
        c='abandon-signet-fallback-'+service+'-1'
        version=run('docker','exec',c,binary,'--version').strip()
        path=run('docker','exec',c,'sh','-c','command -v "$1"','resolve-bin',binary).strip()
        if not path.startswith('/') or '\n' in path:raise ValueError('invalid executable')
        sha=run('docker','exec',c,'sha256sum',path).split()[0]
        match=re.search(r'[0-9a-f]{40}',version)
        if not match or sha!=qualified['binaries'][binary]['sha256']:raise ValueError('unqualified native binary')
        binaries[name]={'version':version,'sha256':sha,'source_head_from_version':match.group()}
        if name in ['captaind','watchmand','barkd','barkd2']:
            running=run('docker','exec',c,'sha256sum','/proc/1/exe').split()[0]
            if running!=sha:raise ValueError('running process differs from executable')
            binaries[name]['running_executable_sha256']=running
    # Public identity and hashes only. The witness directory remains private.
    witness=Path(mounts['/data/captaind']['Source'])/'expiry-payout-witness'
    identity_bytes=read(witness/'identity.json');identity=json.loads(identity_bytes)
    if set(identity)!={'version','genesis','server_pubkey'}:raise ValueError('unexpected witness identity')
    history=read(witness/'history');indexed=history.decode().splitlines()
    if (history and not history.endswith(b'\n')) or len(set(indexed))!=len(indexed):raise ValueError('invalid witness history')
    files=sorted(witness.glob('*.json'))
    if len(files)>513:raise ValueError('witness bound exceeded')
    hashes={};records={}
    for path in files:
        data=read(path);hashes[path.name]=digest(data)
        if path.name!='identity.json':
            txid=path.stem
            if not re.fullmatch('[0-9a-f]{64}',txid):raise ValueError('invalid witness filename')
            records[txid]=json.loads(data)
    if not set(indexed)<=set(records):raise ValueError('missing witness record')
    # One repeatable-read snapshot for the fixed baseline AND all witnessed payouts.
    selected=sorted(set(ids)|set(records))
    if len(selected)>1024:raise ValueError('database receipt bound')
    quoted=','.join("'"+i+"'" for i in selected)
    sql=("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='8s'; "
         "SELECT jsonb_build_object('database',current_database(),'cluster_system_identifier',(SELECT system_identifier::text FROM pg_control_system()),"
         "'nursery_count',(SELECT count(*) FROM nursery_tx),'rows',(SELECT coalesce(jsonb_agg(q),'[]'::jsonb) FROM ("
         "SELECT n.txid,n.kind::text,encode(n.tx,'hex') AS raw_hex,(SELECT coalesce(jsonb_agg(jsonb_build_object("
         "'id',s.id,'spk_hex',encode(s.spk,'hex'),'fee_sat',s.fee_sat) ORDER BY s.id),'[]'::jsonb) "
         "FROM expiry_settlement s WHERE s.txid=n.txid) AS settlements FROM nursery_tx n WHERE n.txid IN ("+quoted+") ORDER BY n.txid) q)); COMMIT;")
    db=parse_sql(run(str(DEPLOY/'fallback/ops.sh'),'sql',sql))
    current={r['txid']:r for r in db.pop('rows')}
    expected=[public_transaction(r) for r in baseline]
    mismatches=[r['txid'] for r in expected if r['txid'] not in current or public_transaction(current[r['txid']])!=r]
    witness_mismatches=[]
    for txid,r in records.items():
        native=[{'id':s['id'],'spk_hex':None if s['spk'] is None else bytes(s['spk']).hex(),'fee_sat':s['fee_sat']} for s in r['settlements']]
        actual=current.get(txid)
        if not actual or actual['kind']!='expiry-payout' or bytes(r['tx']).hex()!=actual['raw_hex'] or settlement_identity(native)!=settlement_identity(actual['settlements']):witness_mismatches.append(txid)
    if read(witness/'history')!=history or read(cfg_path)!=cfg_bytes:raise ValueError('observation changed; retry')
    after={s:container(s) for s in before}
    if any(public_state(before[s])!=public_state(after[s]) for s in before):raise ValueError('service changed; retry')
    if not all(r['State']['Running'] for r in after.values()):raise ValueError('release is not running')
    startup_passed=cfg['expiry_payout']['enabled'] and cap['State'].get('Health',{}).get('Status')=='healthy'
    return {'kind':'release_identity','schema_version':1,'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'producer':{'sha256':digest(read(Path(__file__))),'bridge_sha256':digest(read(Path(__file__).with_name('server-observer.py')))},
            'binaries':binaries,'containers':{s:public_state(r) for s,r in after.items()},
            'settings':{'network':cfg['network'],'ordinary_lifetime':cfg['vtxo_lifetime'],'pool_lifetime':cfg['vtxopool']['vtxo_lifetime'],
                        'payout_enabled':cfg['expiry_payout']['enabled'],'min_payout_sat':cfg['expiry_payout']['min_payout_sat']},
            'guard':{'startup_validation_completed':bool(startup_passed),'basis':'verified native process runs start with payouts enabled and healthy RPC; source lib.rs:365-367 validates witness before workers',
                     'identity':identity,'identity_sha256':digest(identity_bytes),'history_sha256':digest(history),'indexed_count':len(indexed),
                     'record_count':len(records),'record_sha256':hashes,'current_witness_database_mismatches':witness_mismatches},
            'preservation':{'baseline_sha256':digest(baseline_bytes),'baseline_count':len(baseline),'baseline_identity_rows':expected,
                            'current_identity_rows':[public_transaction(current[i]) for i in ids if i in current],'mismatches':mismatches,
                            'inventory_consistent':True,'database':db},
            'complete':False,'validation_scope':'bounded live runtime and one DB snapshot; frozen baseline is trusted operator evidence; no destructive restore, pending-payout upgrade or simulation credit'}

if __name__=='__main__':
    try:
        if len(sys.argv)!=1:raise ValueError('no arguments accepted')
        result=json.dumps(observe(),allow_nan=False)
        if len(result.encode())>MAX_BYTES:raise ValueError('receipt bound exceeded')
        print(result)
    except Exception:
        print('read-only release receipt unavailable',file=sys.stderr);sys.exit(1)
