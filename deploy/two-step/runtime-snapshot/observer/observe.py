#!/usr/bin/python3
import pathlib,subprocess,json,os,hashlib,socket,datetime,base64,urllib.request,time,tomllib,sys,uuid
assert len(sys.argv)==1
os.umask(0o077);assert socket.gethostname()=='two-step-signet-20261014'

projection_source=pathlib.Path('/opt/two-step-a-observer-source-v5')
projection_hashes={'__init__.py': '98bef9de9dff5ea64382b9788a148b32cd6f2bb140ba620f105b357ff0c94971', 'model.py': '8574055927dda49096b169a0a0ff54b7da30a5a5d458173edaa2770b778e05c7', 'bundle_audit.py': 'b398e53b7cc8a145fb4fe534ba1a62664cab936396dd88d6a48a441b31e2e3de', 'private_transaction_projection.py': 'f2634865df3189f05b6e2a5d1fde1a7864d0a762131c83032cf2b3f7fbcfc62a', 'bip39-english.txt': '2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda'}
for source_name,expected_sha in projection_hashes.items():
 assert hashlib.sha256((projection_source/'simulator'/source_name).read_bytes()).hexdigest()==expected_sha
sys.path.insert(0,str(projection_source))
from simulator.private_transaction_projection import project_private_transaction

name='abandon-signet-fallback-two-step-a-captaind-20261015';cfg='/etc/two-step-a/captaind-enrollment.toml';started=datetime.datetime.now(datetime.timezone.utc).isoformat()
def run(argv,timeout=15):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
 return {'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def native(args,addr='127.0.0.1:3536'):
 r=run(['docker','exec',name,'/app/captaind','rpc','--addr',addr]+args)
 if r['exit']==0:
  try:r['data']=json.loads(r['stdout'])
  except json.JSONDecodeError:r['literal_text']=r['stdout']
  del r['stdout']
 return r
cookie=pathlib.Path('/etc/two-step-a/core.cookie').read_bytes().strip();headers={'Authorization':'Basic '+base64.b64encode(cookie).decode(),'Content-Type':'application/json'}
def core(method,params=[]):
 req=urllib.request.Request('http://127.0.0.1:28332',data=json.dumps({'jsonrpc':'2.0','id':'A-observer','method':method,'params':params}).encode(),headers=headers)
 r=json.load(urllib.request.urlopen(req,timeout=15));assert r.get('error') is None;return r['result']
before=core('getblockchaininfo');inspect=json.loads(run(['docker','inspect',name])['stdout'])[0]
pid=inspect['State']['Pid'];assert pid>0
binary_sha256={n:hashlib.sha256(pathlib.Path('/proc/'+str(pid)+'/root/app/'+n).read_bytes()).hexdigest() for n in ['captaind','watchmand','bark','barkd']}
binary_startticks=pathlib.Path('/proc/'+str(pid)+'/stat').read_text().split()[21]
# Explicitly admitted mixed release. A label alone never supplies a source pin.
expected_image='sha256:089396ec2da23eb35e6cfe0477bc505a21c622c9bbd1e7230dcc2e6115cffe68'
expected_source='de9897024bb4b4a534ce359fa1e523104e47207ed6cef022e6b3c2b1299296d7'
expected_server_source='0440f31756ee5da2e2ba796d200e0f6a120c9f2f7f16a544e681b497c23d0744'
expected_retained_source='ba9451d2aac67e0aed930946e9b3a17d64b62c9f497d6e525d46eeded20971c1'
expected_binaries={'captaind':'86362ac8d79d162c66b7724c00a68c9b960129bcd0bc0de176a7b8c4d70e6088','watchmand':'5974ea427ff0033a5f5bb5290866a46bc4a2f5a3469de4d81ca4c3caf4d7bc77','bark':'7c79f757d4027809caf6806dfb0c0c527354e39e73417ceb84a213ad43403b4f','barkd':'df8a44d084465bad57b7ae96eb0ca1a3cf234d6e1cd5e078520d9577c1c7d78b'}
manifest_bytes=pathlib.Path('/proc/'+str(pid)+'/root/app/SOURCE_MANIFEST.json').read_bytes()
assert hashlib.sha256(manifest_bytes).hexdigest()=='af7531bdf618eb07ffa608897a54a33bc013379dcc4d3084908027fc8cf0c799'
manifest=json.loads(manifest_bytes)
assert manifest['source_key']==expected_source and manifest['server_source_key']==expected_server_source and manifest['retained_client_source_key']==expected_retained_source
assert manifest['server_source_commit']=='9a9334d6a18bfcd7a371a10a6fe85ce985dacf97'
assert binary_sha256==expected_binaries and inspect['Image']==expected_image
for component,expected in expected_binaries.items():assert manifest['components'][component]['sha256']==expected
assert inspect['Config']['Entrypoint']==['/app/captaind'] and inspect['Config']['Cmd']==['--config',cfg,'start']
assert pathlib.Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')[:-1]==[b'/app/captaind',b'--config',cfg.encode(),b'start']
assert hashlib.sha256(pathlib.Path(cfg).read_bytes()).hexdigest()=='a6ea55116a0cdfa411daad0a6ecb0e70a4a9b6edb95e489bec64992346e2136f'
watch_name='abandon-signet-fallback-two-step-a-watchmand-bootstrap-20261015'
watch_inspect=json.loads(run(['docker','inspect',watch_name])['stdout'])[0];watch_pid=watch_inspect['State']['Pid'];assert watch_pid>0
watch_ticks=pathlib.Path('/proc/'+str(watch_pid)+'/stat').read_text().split()[21]
assert watch_inspect['State']['Running'] and watch_inspect['Image']==expected_image
assert hashlib.sha256(pathlib.Path('/proc/'+str(watch_pid)+'/root/app/watchmand').read_bytes()).hexdigest()==expected_binaries['watchmand']
watch_cfg='/etc/two-step-a/watchmand.toml'
assert watch_inspect['Config']['Entrypoint']==['/app/watchmand'] and watch_inspect['Config']['Cmd']==['--config',watch_cfg,'start']
assert pathlib.Path('/proc/'+str(watch_pid)+'/cmdline').read_bytes().split(b'\0')[:-1]==[b'/app/watchmand',b'--config',watch_cfg.encode(),b'start']
assert hashlib.sha256(pathlib.Path(watch_cfg).read_bytes()).hexdigest()=='7547450d4eeedac9f3674a06ab70e16a96718c14656839d82d6a467b72d04e16'
identity_bytes=pathlib.Path('/var/lib/two-step-a/server/expiry-payout-witness/identity.json').read_bytes()
assert json.loads(identity_bytes)['server_pubkey']=='033542ed11f00f8f81d6ba2661737ca67d1bcf935c126e5a0c2abfcc577ebdcd7f'

pg='/nix/store/f5bx5vyjvfc52a93q6p7gy7aidwg3jpx-postgresql-17.11/bin/psql';pga=['runuser','-u','two-step-a','--','env','PGPASSFILE=/etc/two-step-a/pgpass',pg,'-h','127.0.0.1','-p','25432','-U','two_step_a','-d','two_step_a_20261015','-At','-v','ON_ERROR_STOP=1','-c']
tables=run(pga+["SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]);assert tables['exit']==0,tables
names=tables['stdout'].splitlines();quoted=['"'+n.replace('"','""')+'"' for n in names]
count_select=' UNION ALL '.join("SELECT '"+n.replace("'","''")+"' AS name, count(*) AS n FROM public."+qn for n,qn in zip(names,quoted))
financial_tables=['recovery_reserve','recovery_receipt','recovery_source_hold','recovery_capital_release','expiry_settlement','expiry_cancelled_participation']
assert all(t in names for t in financial_tables)
financial_parts=[]
private_total="((SELECT coalesce(sum(octet_length(transaction_bytes)),0) FROM public.recovery_receipt)+(SELECT coalesce(sum(octet_length(tx)),0) FROM public.nursery_tx))"
private_body_allowed=private_total+'<=4194304'

for table in financial_tables:
 if table=='recovery_receipt':
  financial_parts.extend(["'recovery_receipt'","(SELECT coalesce(json_agg(row_to_json(z)), '[]'::json) FROM (SELECT id, CASE WHEN "+private_body_allowed+" AND octet_length(transaction_bytes)<=1048576 THEN encode(transaction_bytes,'hex') ELSE NULL END AS transaction_bytes_hex,block_height,block_hash,proof_state,proof_gap FROM public.recovery_receipt ORDER BY id LIMIT 1001) z)"]);continue
 financial_parts.extend(["'"+table+"'", "(SELECT coalesce(json_agg(row_to_json(z)), '[]'::json) FROM (SELECT * FROM public."+table+" ORDER BY id LIMIT 1001) z)"])
financial_parts.extend(["'earliest_round_expiry'","(SELECT min(expiry) FROM public.round)","'earliest_vtxo_expiry'","(SELECT min(expiry) FROM public.vtxo)","'nursery'","(SELECT coalesce(json_agg(row_to_json(z)), '[]'::json) FROM (SELECT txid,CASE WHEN "+private_body_allowed+" AND octet_length(tx)<=1048576 THEN encode(tx,'hex') ELSE NULL END AS signed_transaction_hex,kind,confirm_target_height,confirmed_at_height,abandoned_at,created_at,updated_at FROM public.nursery_tx ORDER BY id LIMIT 1001) z)","'vtxo_states'","(SELECT coalesce(json_agg(row_to_json(z)), '[]'::json) FROM (SELECT vtxo_id,expiry,amount,encode(policy,'hex') AS policy_hex,anchor_point,vtxo_txid,policy_type,spend_state,confirmed_height,onchain_spent_txid FROM public.vtxo ORDER BY vtxo_id LIMIT 1001) z)"])
summary = {
 'earliest_expiry_height': "(SELECT min(expiry) FROM (SELECT expiry FROM public.round UNION ALL SELECT expiry FROM public.vtxo UNION ALL SELECT expiry FROM public.pending_board) e)",
 'customer_principal_sat': "(SELECT coalesce(sum(amount),0) FROM public.vtxo v WHERE policy_type='pubkey' AND spend_state IN ('spendable','unclaimed','unregistered') AND onchain_spent_txid IS NULL AND policy<>decode('0002dfa52f6690299d2d6a08323083e290597b56fee125063e5f4e2957731639c42c','hex') AND NOT EXISTS(SELECT 1 FROM public.expiry_settlement s WHERE s.id=v.vtxo_id))",
 'funding_txids': "(SELECT coalesce(json_agg(txid),'[]'::json) FROM (SELECT txid FROM (SELECT txid FROM public.virtual_transaction WHERE is_funding UNION SELECT funding_txid FROM public.round UNION SELECT txid FROM public.sweep UNION SELECT txid FROM public.nursery_tx) t ORDER BY txid LIMIT 129) z)",
 'conditional_count': "((SELECT count(*) FROM public.htlc_vtxo)+(SELECT count(*) FROM public.lightning_invoice)+(SELECT count(*) FROM public.lightning_payment_attempt)+(SELECT count(*) FROM public.lightning_htlc_subscription))",
 'exit_count': "(SELECT count(*) FROM public.vtxo v WHERE (v.confirmed_height IS NOT NULL AND EXISTS(SELECT 1 FROM public.virtual_transaction t WHERE t.txid=v.vtxo_txid AND NOT t.is_funding)) OR v.onchain_spent_txid IS NOT NULL)",
 'payout_count': "(SELECT count(*) FROM public.expiry_settlement)",
 'recovery_hold_sat': "(SELECT coalesce(sum(held_sat),0) FROM public.recovery_source_hold)",
 'customer_principal_scope': "'Current ordinary pubkey leaf IDs in spendable/unclaimed/unregistered states; excludes exact protocol padding policy, onchain-spent and expiry-settled leaves. Independently verify attribution; not a global economic classification across conditional/delegated histories.'"
}
financial_parts.extend(["'native_recovery_reserve_state'","(SELECT json_build_object('id',r.id,'sweep_spk',encode(r.sweep_spk,'hex'),'checkpoint_height',r.checkpoint_height,'checkpoint_hash',r.checkpoint_hash,'fee_headroom_sat',r.fee_headroom_sat,'held_sat',(SELECT coalesce(sum(held_sat),0) FROM public.recovery_source_hold),'unknown_gross',EXISTS(SELECT 1 FROM public.recovery_receipt WHERE proof_state='unknown_gross')) FROM public.recovery_reserve r WHERE r.id='rounds')"])
financial_parts.extend(["'exploratory_summary'","json_build_object("+','.join("'"+k+"',"+sql for k,sql in summary.items())+")"])
q="BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT json_build_object('tables',(SELECT json_object_agg(name,n) FROM ("+count_select+") x),'financial_details',json_build_object("+','.join(financial_parts)+"),'row_limit',1001); COMMIT;"
counts=run(pga+[q]);assert counts['exit']==0,counts;data=next(json.loads(x) for x in counts['stdout'].splitlines() if x.startswith('{'))
# Preserve the exact original SQL snapshot privately before discarding witness-bearing fields.
raw_private=counts['stdout'].encode();assert len(raw_private)<=12*1024*1024
private_dir=pathlib.Path('/var/lib/two-step-a/private-observer-evidence');private_dir.mkdir(mode=0o700,exist_ok=True)
assert private_dir.stat().st_uid==0 and private_dir.stat().st_mode&0o077==0
private_file=private_dir/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex+'.sql-source.json')
with private_file.open('xb') as f:
 f.write(raw_private);f.flush();os.fsync(f.fileno())
private_file.chmod(0o600)
private_directory_fd=os.open(private_dir,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(private_directory_fd)
finally:os.close(private_directory_fd)
details=data['financial_details'];total_private=0
for table,key,id_key in [('recovery_receipt','transaction_bytes_hex','id'),('nursery','signed_transaction_hex','txid')]:
 rows=details[table];assert isinstance(rows,list) and len(rows)<=1000
 if table=='recovery_receipt':assert len(rows)==data['tables']['recovery_receipt']
 else:assert len(rows)==data['tables']['nursery_tx']
 for row in rows:
  encoded=row.pop(key);assert isinstance(encoded,str) and len(encoded)<=2*1024*1024
  original=bytes.fromhex(encoded);total_private+=len(original);assert total_private<=4*1024*1024
  row.update(project_private_transaction(row[id_key],original))
  if table=='recovery_receipt':
   gap=row.pop('proof_gap');assert gap is None or isinstance(gap,str)
   row['proof_gap_present']=gap is not None
   row['private_proof_gap_sha256']=None if gap is None else hashlib.sha256(gap.encode()).hexdigest()

config=tomllib.loads(pathlib.Path(cfg).read_text());pubpolicy={k:config[k] for k in config if k not in ('postgres','bitcoind','lightning','cln','auth','mnemonic') and not any(t in k.lower() for t in ['password','secret','cookie','token','key','cert'])}
# Recursive redaction protects any private nested authentication fields.
def safe(v):
 if isinstance(v,dict):return {k:safe(x) for k,x in v.items() if not any(t in k.lower() for t in ['password','secret','cookie','token','key','cert'])}
 if isinstance(v,list):return [safe(x) for x in v]
 return v
pubpolicy=safe(pubpolicy)
r={'observed_at':started,'host':socket.gethostname(),'network':'signet','genesis':core('getblockhash',[0]),'core_before':before,'container_inspect':inspect,'binary_sha256':binary_sha256,'binary_identity_pid':pid,'binary_identity_startticks':binary_startticks,'recovery_reserve':data['financial_details']['native_recovery_reserve_state'],'private_financial_snapshot_sha256':hashlib.sha256(raw_private).hexdigest(),'transaction_projection_source_sha256':projection_hashes,'source_key':expected_source,'server_source_key':expected_server_source,'retained_client_source_key':expected_retained_source,'source_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),'server_source_commit':manifest['server_source_commit'],'watchman_process_identity':{'container_id':watch_inspect['Id'],'pid':watch_pid,'startticks':watch_ticks,'image':watch_inspect['Image']},'config_sha256':hashlib.sha256(pathlib.Path(cfg).read_bytes()).hexdigest(),'public_policy':pubpolicy,'wallet_status_omitted':'rpc wallet reveals a newaddress; use read-only inventory','rounds_inventory':native(['wallet-inventory']),'watchman_inventory':native(['wallet-inventory'],'127.0.0.1:3538'),'nursery_all':native(['nursery','list','--include-confirmed','--include-abandoned']),'postgres_public_table_counts':data,'postgres_financial_rows':data['financial_details']['exploratory_summary'],'postgres_count_snapshot':'REPEATABLE READ READ ONLY','signing_possession_proof':None,'missing_signing_proof_reason':'No bounded native read-only signing challenge exists; genuine signed wallet spend may prove possession only after separately authorized financial release.'}
w=pathlib.Path('/home/ubuntu/two-step-a-deploy-staging-20261015/empty-watchman-public-manifest.json')
if w.exists():r['last_stopped_watchman_inventory_snapshot']=json.loads(w.read_text())
r['container_runtime_stable']=json.loads(run(['docker','inspect',name])['stdout'])[0]['State']['Pid']==pid and pathlib.Path('/proc/'+str(pid)+'/stat').read_text().split()[21]==binary_startticks
watch_after=json.loads(run(['docker','inspect',watch_name])['stdout'])[0];assert watch_after['Id']==watch_inspect['Id'] and watch_after['State']['Pid']==watch_pid and pathlib.Path('/proc/'+str(watch_pid)+'/stat').read_text().split()[21]==watch_ticks
r['core_after']=core('getblockchaininfo');r['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();r['stable_tip']=r['core_before']['bestblockhash']==r['core_after']['bestblockhash'];r['complete']=False;r['unqualified_notes']=['source snapshot only; independent reconciliation and policy validation required','normal estimator ceiling is not cumulative fee or urgent reserve enforcement']
out=json.dumps(r);assert len(out.encode())<2*1024*1024;print(out)
