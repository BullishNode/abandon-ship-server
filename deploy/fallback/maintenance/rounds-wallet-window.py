"""Owned signet wallet geometry window, in explicit prepare/commit/resume phases.

Never dispatches a client, tops up funds, restores an old DB, deletes an intent,
or retries signing. An interrupted native invocation leaves its named container
and frozen server for reconciliation. Execute on the target as ubuntu.
"""
import argparse,datetime,fcntl,hashlib,json,os,re,subprocess,sys,time
from decimal import Decimal
from pathlib import Path

ROOT=Path('/home/ubuntu/abandon-signet-fallback')
DEPLOY=ROOT/'deploy'
HEAD='8f2286ee7e1631b0ceccd1631ec3b728a1bb0a52'
FREEZER=ROOT/'tools/freeze-signet-upgrade-8f.py'
SERVICES=['fb-web','fb-web2','fb-api','fb-api2','fb-barkd','fb-barkd2','fb-ark','fb-watchmand','fb-captaind']

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    data=(json.dumps(value,indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
def run(*args):
    p=subprocess.run(args,cwd=DEPLOY,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if p.returncode or len(p.stdout)>8*1024*1024:raise RuntimeError('bounded operator command failed; no automatic retry')
    return p.stdout.decode()
def inspect(name):return json.loads(run('sudo','docker','inspect',name))[0]
def state(service):return inspect('abandon-signet-fallback-'+service+'-1')
def rows():
    sql="""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='8s';
SELECT coalesce(jsonb_agg(jsonb_build_object('txid',n.txid,'kind',n.kind::text,'raw_hex',encode(n.tx,'hex'),
'confirmed_at_height',n.confirmed_at_height,'abandoned_at',n.abandoned_at,
'settlements',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',s.id,'spk_hex',encode(s.spk,'hex'),'fee_sat',s.fee_sat) ORDER BY s.id),'[]'::jsonb)
FROM expiry_settlement s WHERE s.txid=n.txid)) ORDER BY n.txid),'[]'::jsonb) FROM nursery_tx n; COMMIT;"""
    arrays=[json.loads(s) for s in run('./fallback/ops.sh','sql',sql).splitlines() if s.startswith('[')]
    if len(arrays)!=1:raise RuntimeError('invalid nursery inventory')
    return arrays[0]
def identity(row):return {k:row[k] for k in ['txid','kind','raw_hex','settlements']}
def coordinated(path):
    c=json.loads(path.read_text())
    assert c['simulator_dispatch_paused'] is True
    assert c['inflight_actions']==c['unknown_actions']==c['recovery_inflight_or_unknown']==0
    assert c.get('funding_inflight_or_unknown')==0
    assert c['target_vm']=='172.81.182.157' and c['target_head']==HEAD
    observed=datetime.datetime.fromisoformat(c['observed_utc']);assert observed.tzinfo
    age=(datetime.datetime.now(datetime.timezone.utc)-observed).total_seconds();assert 0<=age<600
    return c
def stopped(frozen):
    volumes={v['source'] for v in frozen['volumes'].values()}
    for service in SERVICES:
        s=state(service);assert not s['State']['Running'] and s['State']['Pid']==0
        assert s['Image']==frozen['before_services'][service]['image']
    # Reject a different live container sharing any of the frozen wallet volumes.
    ids=run('sudo','docker','ps','-q').splitlines()
    for container in ids:
        s=inspect(container)
        assert not volumes.intersection(m['Source'] for m in s['Mounts']), 'another wallet writer/container shares frozen data'
def intents(directory):
    return [json.loads(line) for line in directory.read_text().splitlines()]
def restart(frozen):
    cap='fb-captaind'
    if frozen['before_services'][cap]['running']:
        run('sudo','docker','start','abandon-signet-fallback-'+cap+'-1')
        deadline=time.monotonic()+120
        while True:
            s=state(cap)
            if s['State'].get('Health',{}).get('Status')=='healthy':break
            assert s['State']['Running'] and time.monotonic()<deadline, 'guard/runtime health not established; keep dependents stopped'
            time.sleep(1)
    for service in reversed(SERVICES[:-1]):
        if frozen['before_services'][service]['running']:
            # Start exactly the preserved container; never recreate an image/config.
            run('sudo','docker','start','abandon-signet-fallback-'+service+'-1')

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','commit','resume-unchanged'])
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--coordination',type=Path,required=True)
    args=parser.parse_args()
    assert re.fullmatch('[a-zA-Z0-9_-]{1,64}',args.run_id)
    # Serialize our own operator phases. Never delete this stable lock file;
    # an existing captaind still obeys the separate one-writer runbook rule.
    lock=(ROOT/'.rounds-wallet-maintenance.lock').open('a')
    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    c=coordinated(args.coordination);request=json.loads(args.request.read_text())
    assert request['head']==HEAD and request['run_id']==args.run_id
    assert request['native_binary_sha256']==digest(Path(request['native_binary_path']))
    assert 1<=request['output_count']<=32 and request['chunk_sat']>=1000
    assert request['fee_sat_per_vb']>0 and request['max_fee_sat']>0
    out=ROOT/'maintenance'/args.run_id
    if args.mode=='prepare':
        before=rows()
        assert all(r['confirmed_at_height'] is not None or r['abandoned_at'] is not None for r in before), 'wallet geometry waits for active nursery confirmations before freezing'
        out.mkdir(parents=True,mode=0o700,exist_ok=False)
        save(out/'request.json',request);save(out/'coordination-prepare.json',c)
        # Existing freezer verifies identity, all writer PIDs, backups and retained
        # raw transactions/settlements; it intentionally leaves the stack stopped.
        with (out/'freeze-private.log').open('xb') as log:
            subprocess.run([sys.executable,str(FREEZER),'--coordination',str(args.coordination),'--output',str(out/'freeze')],cwd=DEPLOY,stdout=log,stderr=log,check=True)
    else:
        assert request==json.loads((out/'request.json').read_text()), 'never change a declared native action'
        save(out/('coordination-'+args.mode+'.json'),c)
    frozen=json.loads((out/'freeze/freeze-result.json').read_text());stopped(frozen)
    prior={r['txid']:r for r in json.loads((out/'freeze/frozen-nursery.json').read_text())}
    current={r['txid']:r for r in rows()}
    for txid,r in prior.items():assert identity(current[txid])==identity(r)
    if args.mode=='resume-unchanged':
        assert set(current)==set(prior), 'new committed native work must be reconciled instead of discarded'
        for p in out.glob('native-*-intent.jsonl'):
            assert not any(v['state'] in ['signed_not_committed','committed_pending_broadcast','broadcast_requested'] for v in intents(p)), 'signed outcome is not a no-spend resume'
        for name in run('sudo','docker','ps','--format','{{.Names}}').splitlines():
            assert name not in {'rounds-maintenance-'+args.run_id+'-plan','rounds-maintenance-'+args.run_id+'-commit'}, 'native operation still running'
        restart(frozen);save(out/'resume-unchanged-result.json',{'result':'PRIOR_CONTAINERS_START_REQUESTED_WITHOUT_NATIVE_SPEND','qualification':'not a fragmentation pass'})
        return
    if args.mode=='commit':
        original_plan=json.loads((out/'plan-result.json').read_text())
        assert original_plan['result']=='NATIVE_PLAN_ONLY_WRITERS_REMAIN_STOPPED', 'a successful prior native plan is mandatory before signing'
        plan_container=inspect('rounds-maintenance-'+args.run_id+'-plan')
        assert not plan_container['State']['Running'] and plan_container['State']['ExitCode']==0
    assert set(current)==set(prior), 'unexpected nursery write while frozen'
    assert all(r['confirmed_at_height'] is not None or r['abandoned_at'] is not None for r in current.values())
    cap=state('fb-captaind');assert cap['Image']==c['target_identity']['image_digest']
    network='abandon-signet-fallback_default';assert network in cap['NetworkSettings']['Networks']
    mode='plan' if args.mode=='prepare' else 'commit'
    declaration={k:request[k] for k in ['rounds_xpub','inputs','expected_input_sat','output_count','chunk_sat','fee_sat_per_vb','max_fee_sat','max_derivation_index']}
    declaration.update(observed_unix=int(datetime.datetime.now(datetime.timezone.utc).timestamp()),simulator_dispatch_paused=True,inflight_actions=0,unknown_actions=0,server_stopped=True)
    native_decl=out/('native-'+mode+'-declaration.json');save(native_decl,declaration)
    volume=next(m for m in cap['Mounts'] if m['Destination']=='/data/captaind');assert volume['Type']=='volume'
    cfg=DEPLOY/'state/signet-fallback/captaind.toml'
    name='rounds-maintenance-'+args.run_id+'-'+mode
    command=['sudo','docker','create','--name',name,'--user','1000:1000','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--network',network,
             '--mount','type=bind,src='+request['native_binary_path']+',dst=/maintenance-native,readonly',
             '--mount','type=bind,src='+str(cfg)+',dst=/config/captaind.toml,readonly',
             '--mount','type=volume,src='+volume['Name']+',dst=/data/captaind,readonly',
             '--mount','type=bind,src='+str(out)+',dst=/intent',
             '--entrypoint','/maintenance-native',cap['Image'],'--'+mode,'/config/captaind.toml','/intent/'+native_decl.name,'/intent/native-'+mode+'-intent.jsonl']
    container_id=run(*command).strip();save(out/('native-'+mode+'-container.json'),{'name':name,'id':container_id,'image':cap['Image']})
    for n in cap['NetworkSettings']['Networks']:
        if n!=network:run('sudo','docker','network','connect',n,name)
    # No automatic retry or stop on uncertain execution. Its recorded named
    # container is the authoritative handle for a subsequent reconciliation.
    with (out/('native-'+mode+'-private.log')).open('xb') as log:
        result=subprocess.run(['sudo','docker','start','-a',name],cwd=DEPLOY,stdout=log,stderr=log)
    execution=inspect(name);assert not execution['State']['Running'] and execution['State']['Pid']==0
    assert result.returncode==0 and execution['State']['ExitCode']==0, 'native failure retained; writers remain stopped'
    journal=intents(out/('native-'+mode+'-intent.jsonl'))
    planned=next(v for v in journal if v['state']=='planned')
    assert planned['fee_sat']<=request['max_fee_sat'] and len(planned['outputs'])==request['output_count']
    if mode=='plan':
        assert {r['txid']:identity(r) for r in rows()}=={k:identity(r) for k,r in prior.items()}
        save(out/'plan-result.json',{'result':'NATIVE_PLAN_ONLY_WRITERS_REMAIN_STOPPED','plan':planned,'qualification':'no signing/broadcast or fragmentation credit'})
        return
    original_plan=json.loads((out/'plan-result.json').read_text())['plan']
    assert all(planned[k]==original_plan[k] for k in ['inputs','outputs','fee_sat']), 'frozen geometry differs from plan; signed state must be reconciled'
    assert journal[-1]['state']=='broadcast_requested'
    txid=journal[-1]['txid'];signed=next(v for v in journal if v['state']=='signed_not_committed')
    now={r['txid']:r for r in rows()};assert set(now)-set(prior)=={txid}
    assert now[txid]['kind']=='internal' and now[txid]['raw_hex']==signed['raw_hex'] and now[txid]['settlements']==[]
    for key,row in prior.items():assert identity(now[key])==identity(row)
    core=json.loads(run('./fallback/ops.sh','cli','getrawtransaction',txid,'true'),parse_float=Decimal)
    assert core['hex']==signed['raw_hex']
    chain_outputs=[{'spk':o['scriptPubKey']['hex'],'sat':int(o['value']*100000000)} for o in core['vout']]
    assert chain_outputs==planned['outputs'] and sum(o['sat'] for o in chain_outputs)+planned['fee_sat']==request['expected_input_sat']
    save(out/'commit-result.json',{'result':'NATIVE_SELF_TRANSFER_COMMITTED_BROADCAST_OBSERVED','txid':txid,'plan':planned,'confirmations':core.get('confirmations',0),'qualification':'recorded self-transfer; confirmation/input-conflict workload/geometry-restoration still required'})
    restart(frozen)
    print(json.dumps({'result':'PRIOR_CONTAINERS_START_REQUESTED_AFTER_RECORDED_SELF_TRANSFER','txid':txid}))

if __name__=='__main__':main()
