#!/usr/bin/env python3
"""Forced SSH command: fixed read-only fallback operations, bounded output.

No arbitrary shell/SQL/path/RPC names accepted. This bridge has no client seed or
mailbox key. Independent chain verification is performed on the simulator host.
"""
import datetime,hashlib,json,os,re,selectors,subprocess,sys,time,tomllib
from decimal import Decimal
from pathlib import Path
DEPLOY=Path('/home/ubuntu/abandon-signet-fallback/deploy')
CONTAINER='abandon-signet-fallback-fb-captaind-1'
MAX_BYTES=2*1024*1024
DESCRIPTOR=Path(__file__).with_name('rounds-wallet-descriptor.json')
WALLET_DECLARATIONS=Path(__file__).with_name('treasury-wallet-declarations.json')
FINANCIAL_ROW_LIMIT=2000
FINANCIAL_TABLES={'coins','pending_boards','links','records','pool','lightning_nodes',
    'htlcs','receives','payments','participations','participation_inputs','participation_outputs'}
READ_DEADLINE=None

def run(argv,timeout=20):
    remaining=timeout if READ_DEADLINE is None else min(timeout,READ_DEADLINE-time.monotonic())
    if remaining<=0:raise ValueError('financial observation deadline exceeded')
    deadline=time.monotonic()+remaining;data=bytearray()
    with subprocess.Popen(argv,cwd=DEPLOY,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ)
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0 or not selector.select(remaining):raise ValueError('bridge deadline exceeded')
                    chunk=os.read(child.stdout.fileno(),min(4096,MAX_BYTES+1-len(data)))
                    if not chunk:break
                    data.extend(chunk)
                    if len(data)>MAX_BYTES:raise ValueError('bridge output bound exceeded')
            child.wait(timeout=max(0.001,deadline-time.monotonic()))
            if child.returncode!=0:raise subprocess.CalledProcessError(child.returncode,argv)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    return data.decode()

def ops(*args):return run([str(DEPLOY/'fallback/ops.sh'),*args])

def request(raw):
    if len(raw)>4096:raise ValueError('request bound exceeded')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('invalid bridge request')
    if value.get('kind') in {'financial_readiness','claimant_inventory'}:
        if set(value)!={'kind'}:raise ValueError('financial operation takes no arguments')
        return value['kind'],None
    if value.get('kind')=='rounds_descriptor':
        if set(value)!={'kind'}:raise ValueError('descriptor takes no arguments')
        return 'rounds_descriptor',None
    if value.get('kind')=='logs':
        if set(value)-{'kind','service','seconds','tail','max_bytes'}:raise ValueError('invalid logs request')
        if value.get('service') not in {'captaind','watchmand'}:raise ValueError('unsupported log service')
        for field,default,lower,upper in [('seconds',300,1,86400),('tail',200,1,1000),('max_bytes',65536,4096,262144)]:
            value.setdefault(field,default)
            if type(value[field]) is not int or not lower<=value[field]<=upper:raise ValueError('log request bound')
        return 'logs',value
    if value.get('kind')=='binding_check':
        if set(value)!={'kind','mailboxes','keys'}:raise ValueError('invalid binding request')
        for field,limit in [('mailboxes',2),('keys',32)]:
            items=value[field]
            if not isinstance(items,list) or len(items)>limit or len(set(items))!=len(items):raise ValueError('binding request bound')
            if any(not isinstance(item,str) or not re.fullmatch(r'(02|03)[0-9a-f]{64}',item) for item in items):raise ValueError('invalid public key')
        if not value['mailboxes']:raise ValueError('protected mailbox required')
        return 'binding_check',value
    if set(value)-{'kind','after'}:raise ValueError('invalid bridge request')
    kind=value.get('kind','snapshot');after=value.get('after','')
    if kind not in {'snapshot','settlements','coins','pending','links','records','nursery'}:raise ValueError('unsupported bridge operation')
    if not isinstance(after,str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{0,128}',after):raise ValueError('invalid cursor')
    if kind=='snapshot' and after:raise ValueError('snapshot takes no cursor')
    return kind,after

def logs(value):
    ending=datetime.datetime.now(datetime.timezone.utc)
    beginning=ending-datetime.timedelta(seconds=value['seconds'])
    container='abandon-signet-fallback-fb-'+value['service']+'-1'
    command=['sudo','-n','docker','logs','--since',beginning.isoformat(),
        '--until',ending.isoformat(),'--tail',str(value['tail']),container]
    data=bytearray();truncated=False;timed_out=False
    # Read a finite Docker log request with a byte and wall-clock limit.
    # Kill only this child command if it exceeds either bound.
    with subprocess.Popen(command,cwd=DEPLOY,stdout=subprocess.PIPE,stderr=subprocess.STDOUT) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ)
                deadline=time.monotonic()+20
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:timed_out=True;break
                    if not selector.select(remaining):timed_out=True;break
                    chunk=os.read(child.stdout.fileno(),min(4096,value['max_bytes']+1-len(data)))
                    if not chunk:break
                    data.extend(chunk)
                    if len(data)>value['max_bytes']:truncated=True;break
            if truncated or timed_out:child.kill()
            child.wait(timeout=5)
            if child.returncode!=0 and not (truncated or timed_out):raise ValueError('logs unavailable')
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    text=bytes(data[:value['max_bytes']]).decode(errors='replace')
    # Keep public event details, but remove named credentials and preimages.
    sensitive=re.compile(r'(secret|password|rpc_pass|private[_ ]key|mailbox[_ ]key|preimage|mnemonic|authorization|credential)',re.I)
    def redact(item):
        if isinstance(item,dict):return {k:('[redacted]' if sensitive.search(k) else redact(v)) for k,v in item.items()}
        if isinstance(item,list):return [redact(v) for v in item]
        if isinstance(item,str):return '[redacted sensitive text]' if sensitive.search(item) else item
        return item
    lines=[]
    for line in text.splitlines():
        try:lines.append(json.dumps(redact(json.loads(line)),allow_nan=False))
        except (ValueError,TypeError):lines.append(redact(line))
    return {'kind':'logs','service':value['service'],'from_utc':beginning.isoformat(),
        'until_utc':ending.isoformat(),'tail':value['tail'],'max_bytes':value['max_bytes'],
        'source_bytes':min(len(data),value['max_bytes']),'truncated':truncated,'timed_out':timed_out,
        'lines':lines,'complete':False,'validation_scope':'bounded read-only log tail; gaps and truncation must not count as complete event coverage'}

def page(kind,after):
    # Lexicographic cyclic inventories: the caller restarts from '' after each
    # complete scan. Never assert a complete consistent database snapshot across
    # separate calls, and never use this cursor as a once-only commit watermark.
    tables={
      'settlements':("s.id", "expiry_settlement s", "s.id,s.txid,s.fee_sat,encode(s.spk,'hex') AS spk,s.created_at, (SELECT COUNT(*) FROM expiry_settlement z WHERE z.txid=s.txid) AS tx_settlement_count, "
         "(SELECT coalesce(jsonb_agg(q),'[]'::jsonb) FROM (SELECT h.spend_state,h.policy_type,h.oor_spent_txid,h.spent_in_round,h.offboarded_in,h.confirmed_height,h.onchain_spent_txid FROM vtxo_history h WHERE h.vtxo_id=s.id AND h.history_created_at=s.created_at) q) AS pre_commit_rows, "
         "EXISTS (SELECT 1 FROM pending_board p WHERE p.vtxo_id=s.id) AS board_reserved, "
         "(SELECT jsonb_build_object('payment_hash',h.payment_hash,'direction',h.direction,'htlc_expiry',h.htlc_expiry,'offchain_resolution',h.offchain_resolution,'chain_resolution',h.chain_resolution, "
         "'collection_recorded',EXISTS (SELECT 1 FROM htlc_settlement z WHERE z.payment_hash=h.payment_hash AND z.created_at<=s.created_at), "
         "'subscription_settled',EXISTS (SELECT 1 FROM lightning_htlc_subscription r WHERE r.payment_hash=h.payment_hash AND r.status='settled')) FROM htlc_vtxo h JOIN vtxo v ON v.id=h.id WHERE v.vtxo_id=s.id) AS htlc"),
      'coins':("v.vtxo_id","vtxo v","v.vtxo_id,v.id,v.expiry,v.spend_state,v.policy_type,encode(v.vtxo,'hex') AS signed_vtxo,v.onchain_spent_txid,v.onchain_spent_height,v.confirmed_height,v.oor_spent_txid,v.spent_in_round,v.offboarded_in"),
      'pending':("p.vtxo_id","pending_board p","p.vtxo_id,p.id,p.expiry,encode(p.vtxo,'hex') AS signed_vtxo"),
      'links':("encode(l.user_pubkey,'hex')","key_link l","encode(l.user_pubkey,'hex') AS user_pubkey,encode(l.mailbox_pk,'hex') AS mailbox_pk,l.child_index"),
      'records':("encode(r.mailbox_pk,'hex')","fallback_record r","encode(r.mailbox_pk,'hex') AS mailbox_pk,encode(r.spk,'hex') AS spk,encode(r.account_xpub,'hex') AS account_xpub,encode(r.mailbox_sig,'hex') AS mailbox_sig,encode(r.account_sig,'hex') AS account_sig"),
      'nursery':("n.txid","nursery_tx n","n.txid,encode(n.tx,'hex') AS raw_tx,n.kind,n.confirmed_at_height,n.abandoned_at")}
    key,table,columns=tables[kind]
    # Cursor whitelist contains no quotes/operators. All table/column names fixed.
    sql=f"BEGIN READ ONLY; SET LOCAL statement_timeout='8s'; SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT {columns} FROM {table} WHERE {key} > '{after}' ORDER BY {key} LIMIT 101) t; COMMIT;"
    lines=ops('sql',sql).splitlines()
    arrays=[line for line in lines if line.startswith('[')]
    if len(arrays)!=1:raise ValueError('unrecognized bridge database response')
    rows=json.loads(arrays[0]);more=len(rows)>100;rows=rows[:100]
    field={'settlements':'id','coins':'vtxo_id','pending':'vtxo_id','links':'user_pubkey','records':'mailbox_pk','nursery':'txid'}[kind]
    return {'kind':kind,'fallback_protocol':3,'rows':rows,'next':rows[-1][field] if more else None,'inventory_consistent':False,
        'validation_scope':'trusted server claims; independently verify chain, backing, signatures and rightful ownership'}

def claimant_sql():
    # Complete bounded candidate tables in ONE snapshot, including spent logical
    # ancestors whose physical backing remains. These are not summed liabilities:
    # delegated predecessors/replacements and internal HTLCs need arbitration.
    tables={
        'coins':("vtxo", "vtxo_id", "vtxo_id,amount,expiry,spend_state,policy_type,encode(policy,'hex') AS policy,anchor_point,vtxo_txid,oor_spent_txid,spent_in_round,offboarded_in,frontier_at,confirmed_height", "onchain_spent_txid IS NULL"),
        'pending_boards':("pending_board", "vtxo_id", "vtxo_id,expiry,encode(vtxo,'hex') AS signed_vtxo", "true"),
        'links':("key_link", "user_pubkey", "encode(user_pubkey,'hex') AS user_pubkey,encode(mailbox_pk,'hex') AS mailbox_pk,child_index", "true"),
        'records':("fallback_record", "mailbox_pk", "encode(mailbox_pk,'hex') AS mailbox_pk,encode(spk,'hex') AS spk,encode(account_xpub,'hex') AS account_xpub,encode(mailbox_sig,'hex') AS mailbox_sig,encode(account_sig,'hex') AS account_sig", "true"),
        'pool':("vtxo_pool", "id", "id,vtxo_id,expiry_height,amount", "spent_at IS NULL"),
        'lightning_nodes':("lightning_node", "id", "id,encode(pubkey,'hex') AS pubkey", "true"),
        'htlcs':("htlc_vtxo h", "h.id", "h.id,h.payment_hash,h.htlc_expiry,h.direction,h.offchain_resolution,h.chain_resolution,h.chain_resolution_height,EXISTS (SELECT 1 FROM htlc_settlement s WHERE s.payment_hash=h.payment_hash) AS collection_recorded", "EXISTS (SELECT 1 FROM vtxo v WHERE v.id=h.id AND v.onchain_spent_txid IS NULL)"),
        'receives':("lightning_htlc_subscription s", "s.id", "s.id,s.lightning_node_id,s.payment_hash,s.status,s.lowest_incoming_htlc_expiry,s.accepted_at,s.final_amount_msat,s.receiver_mailbox_id", "s.status NOT IN ('settled','canceled') OR EXISTS (SELECT 1 FROM vtxo v WHERE v.lightning_htlc_subscription_id=s.id AND v.onchain_spent_txid IS NULL)"),
        'payments':("lightning_payment_attempt p", "p.id", "p.id,p.lightning_node_id,p.payment_hash,p.status,p.amount_msat,p.final_amount_msat,p.sender_mailbox_id,p.lightning_htlc_subscription_id,p.block_height,p.user_fee_sat,p.invoice_expires_at", "p.status NOT IN ('succeeded','failed') OR EXISTS (SELECT 1 FROM lightning_payment_attempt_htlc_vtxo a JOIN vtxo v ON v.vtxo_id=a.vtxo_id WHERE a.lightning_payment_attempt_id=p.id AND v.onchain_spent_txid IS NULL)"),
        'participations':("round_participation p", "p.id", "p.id,p.round_id,p.created_at,p.forfeited_at,p.scheduled_height,(p.unlock_preimage IS NOT NULL) AS unlock_available", "p.forfeited_at IS NULL OR EXISTS (SELECT 1 FROM vtxo v WHERE v.spent_in_round=p.id AND v.onchain_spent_txid IS NULL)"),
        'participation_inputs':("round_part_input i", "i.participation_id,i.vtxo_id", "i.participation_id,i.vtxo_id,(i.signed_forfeit_tx IS NOT NULL) AS forfeit_signed", "EXISTS (SELECT 1 FROM round_participation p WHERE p.id=i.participation_id AND (p.forfeited_at IS NULL OR EXISTS (SELECT 1 FROM vtxo v WHERE v.spent_in_round=p.id AND v.onchain_spent_txid IS NULL)))"),
        'participation_outputs':("round_part_output o", "o.participation_id,o.policy,o.amount", "o.participation_id,encode(o.policy,'hex') AS policy,o.amount,o.unblinded_mailbox_id", "EXISTS (SELECT 1 FROM round_participation p WHERE p.id=o.participation_id AND (p.forfeited_at IS NULL OR EXISTS (SELECT 1 FROM vtxo v WHERE v.spent_in_round=p.id AND v.onchain_spent_txid IS NULL)))"),
    }
    fields=[]
    for name,(table,key,columns,where) in tables.items():
        fields.append("'"+name+"',(SELECT coalesce(jsonb_agg(q),'[]'::jsonb) FROM (SELECT "+columns+" FROM "+table+" WHERE "+where+" ORDER BY "+key+" LIMIT "+str(FINANCIAL_ROW_LIMIT+1)+") q)")
    return "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='8s'; SELECT jsonb_build_object("+','.join(fields)+"); COMMIT;"

def claimant_inventory():
    lines=[line for line in ops('sql',claimant_sql()).splitlines() if line.startswith('{')]
    if len(lines)!=1:raise ValueError('invalid financial database response')
    result=json.loads(lines[0])
    if not isinstance(result,dict) or set(result)!=FINANCIAL_TABLES or any(not isinstance(rows,list) for rows in result.values()):
        raise ValueError('invalid financial inventory shape')
    truncated=[name for name,rows in result.items() if len(rows)>FINANCIAL_ROW_LIMIT]
    if any(len(rows)>FINANCIAL_ROW_LIMIT+1 for rows in result.values()):raise ValueError('financial row bound exceeded')
    return {'kind':'claimant_inventory','fallback_protocol':3,
        'rows':{name:rows[:FINANCIAL_ROW_LIMIT] for name,rows in result.items()},
        'row_limit_per_table':FINANCIAL_ROW_LIMIT,'truncated_tables':sorted(truncated),
        'inventory_consistent':True,'candidate_tables_complete':not truncated,
        'complete':False,'liability_accounting_complete':False,
        'validation_scope':'one database candidate snapshot, NOT a resolved claimant ledger or physical asset inventory; decode policies to exclude padding; independently arbitrate predecessors, HTLCs and backing membership'}

def fee_rate_sat_kvb(value):
    if value is None:return None
    if not isinstance(value,str):raise ValueError('explicit fee rate unit required')
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*sat/(kvb|vb)',value)
    if not match:raise ValueError('unsupported fee rate unit')
    number=Decimal(match[1])*(1000 if match[2]=='vb' else 1)
    if number!=number.to_integral_value() or not 0<number<=10**9:raise ValueError('fee rate out of bound')
    return int(number)

def disk_financial_config(service):
    path=DEPLOY/'state/signet-fallback'/f'{service}.toml'
    with path.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('config file bound exceeded')
    cfg=tomllib.loads(raw.decode());est=cfg.get('fee_estimator',{})
    return {'sha256':hashlib.sha256(raw).hexdigest(),
        'network':cfg.get('network'),'vtxo_lifetime':cfg.get('vtxo_lifetime'),
        'min_trusted_confs':cfg.get('min_trusted_confs'),'round_interval':cfg.get('round_interval'),
        'estimator_max_fee_rate_sat_kvb':fee_rate_sat_kvb(est.get('max_fee_rate')),
        'estimator_fallback_fee_rates_sat_kvb':{name:fee_rate_sat_kvb(est.get('fallback_fee_rate_'+name)) for name in ('fast','regular','slow')},
        'watchman_incremental_relay_fee_sat_kvb':fee_rate_sat_kvb(cfg.get('watchman',{}).get('incremental_relay_fee')),
        'runtime_loaded_identity_verified':False,
        'validation_scope':'allowlisted disk configuration only; defaults, command overrides, loaded runtime identity and transaction-path ceilings are not inferred'}

def public_wallet_declarations(genesis):
    if not WALLET_DECLARATIONS.exists():return {'available':False,'verified':False,'wallets_declared_complete':False,'wallets':[]}
    with WALLET_DECLARATIONS.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('wallet declaration bound exceeded')
    declaration=json.loads(raw)
    if set(declaration)!={'network','genesis','wallets'} or declaration['network']!='signet' or declaration['genesis']!=genesis:
        raise ValueError('wallet declaration network mismatch')
    wallets=declaration['wallets']
    if not isinstance(wallets,list) or not 1<=len(wallets)<=2:raise ValueError('wallet declaration bound')
    seen=set();member_ids=set();identities=set();declared_descriptors=set()
    for wallet in wallets:
        if set(wallet)!={'member_id','wallet_kind','identity','descriptors'} or wallet['wallet_kind'] not in {'rounds','watchman'} or wallet['wallet_kind'] in seen:
            raise ValueError('invalid public wallet identity')
        seen.add(wallet['wallet_kind'])
        if any(not isinstance(wallet[key],str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,128}',wallet[key]) for key in ('member_id','identity')):
            raise ValueError('invalid public wallet identifier')
        if wallet['member_id'] in member_ids or wallet['identity'] in identities:raise ValueError('duplicate public wallet identity')
        member_ids.add(wallet['member_id']);identities.add(wallet['identity'])
        descriptors=wallet['descriptors']
        if not isinstance(descriptors,list) or not 1<=len(descriptors)<=2:raise ValueError('descriptor bound')
        for descriptor in descriptors:
            # Refuse private descriptors BEFORE even the local Core RPC.
            if not isinstance(descriptor,str) or len(descriptor)>1024 or not re.fullmatch(r'[a-zA-Z0-9\[\]/(),#*<>;\x27]+',descriptor):
                raise ValueError('invalid public descriptor characters')
            if re.search(r'xprv|tprv|(?:^|[^1-9A-HJ-NP-Za-km-z])[59cKL][1-9A-HJ-NP-Za-km-z]{50,51}(?:[^1-9A-HJ-NP-Za-km-z]|$)',descriptor):
                raise ValueError('private descriptor refused')
            if descriptor in declared_descriptors:raise ValueError('same public descriptor counted twice')
            declared_descriptors.add(descriptor)
            details=json.loads(ops('cli','getdescriptorinfo',descriptor))
            if details.get('hasprivatekeys') is not False or details.get('descriptor')!=descriptor or details.get('isrange') is not True:
                raise ValueError('canonical public ranged descriptor required')
    return {'available':True,'verified':True,'wallets_declared_complete':seen=={'rounds','watchman'},'wallets':wallets,
        'validation_scope':'canonical public descriptor syntax/network declaration verified; daemon ownership and current signer inventory still require independent runtime proof'}

# Literal existing containers/admin sockets. Do not source deployment .env,
# read private config/keymaps, invoke status(), or accept request-selected argv.
WALLET_SOURCES={
    'rounds':('captaind','abandon-signet-fallback-fb-captaind-1','127.0.0.1:3536'),
    'watchman':('watchmand','abandon-signet-fallback-fb-watchmand-1','127.0.0.1:3538'),
}
WALLET_FIELDS={'wallet','network','public_descriptor','tip_height','tip_hash',
    'min_trusted_confirmations','utxos','complete','reserved_outpoints','last_revealed_index'}
UTXO_FIELDS={'outpoint','value_sat','script_pubkey','keychain','derivation_index',
    'confirmed_height_upper_bound','confirmation_block_hash','unconfirmed_first_seen','unconfirmed_last_seen',
    'coinbase','confirmations','mature','trusted','reserved','transaction_eligible','confirmed_fee_eligible',
    'confirming_descendant_txid','confirmation_time'}


def bounded_integer(value,upper,nullable=False):
    if nullable and value is None:return
    if type(value) is not int or not 0<=value<=upper:raise ValueError('invalid public wallet integer')


def public_hash(value,nullable=False):
    if nullable and value is None:return
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):raise ValueError('invalid public wallet hash')


def public_outpoint(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}:(0|[1-9][0-9]{0,9})',value):
        raise ValueError('invalid public wallet outpoint')
    bounded_integer(int(value.split(':')[1]),2**32-1)


def validate_wallet_frame(value,owner,network):
    # Reject unknown fields, especially private additions, instead of exporting
    # or silently sanitizing a partial source as a complete wallet snapshot.
    if not isinstance(value,dict) or set(value)!={'rounds','watchman','complete','missing_sources'} or value['complete'] is not False:
        raise ValueError('invalid owning-wallet response')
    opposite='watchman' if owner=='rounds' else 'rounds'
    expected=('watchmand owns its wallet; inventory unavailable from captaind' if owner=='rounds' else
        'captaind owns its rounds wallet; collect its separate source without assuming global atomicity')
    if value[opposite] is not None or value['missing_sources']!=[expected]:raise ValueError('invalid separate-owner scope')
    wallet=value[owner]
    if not isinstance(wallet,dict) or set(wallet)!=WALLET_FIELDS or wallet['wallet']!=owner or wallet['network']!=network or wallet['complete'] is not True:
        raise ValueError('incomplete or incompatible owning-wallet snapshot')
    descriptor=wallet['public_descriptor']
    if not isinstance(descriptor,str) or len(descriptor)>1024 or not re.fullmatch(r'[a-zA-Z0-9\[\]/(),#*<>;\x27]+',descriptor):
        raise ValueError('invalid public descriptor')
    if re.search(r'xprv|tprv|(?:^|[^1-9A-HJ-NP-Za-km-z])[59cKL][1-9A-HJ-NP-Za-km-z]{50,51}(?:[^1-9A-HJ-NP-Za-km-z]|$)',descriptor):
        raise ValueError('private descriptor refused')
    bounded_integer(wallet['tip_height'],2**32-1);public_hash(wallet['tip_hash'])
    bounded_integer(wallet['min_trusted_confirmations'],2**32-1)
    if wallet['min_trusted_confirmations']==0:raise ValueError('invalid wallet trust threshold')
    bounded_integer(wallet['last_revealed_index'],2**32-1,True)
    rows=wallet['utxos'];reserved=wallet['reserved_outpoints']
    if not isinstance(rows,list) or len(rows)>FINANCIAL_ROW_LIMIT or not isinstance(reserved,list) or len(reserved)>FINANCIAL_ROW_LIMIT:
        raise ValueError('owning-wallet row budget exceeded')
    for point in reserved:public_outpoint(point)
    if len(set(reserved))!=len(reserved):raise ValueError('duplicate wallet reservation')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!=UTXO_FIELDS:raise ValueError('invalid public UTXO fields')
        public_outpoint(row['outpoint'])
        if row['outpoint'] in seen:raise ValueError('duplicate wallet UTXO')
        seen.add(row['outpoint'])
        bounded_integer(row['value_sat'],21_000_000*100_000_000)
        bounded_integer(row['derivation_index'],2**32-1);bounded_integer(row['confirmations'],2**32-1)
        if row['keychain'] not in {'external','internal'} or not isinstance(row['script_pubkey'],str) or not re.fullmatch(r'(?:[0-9a-f]{2}){1,10000}',row['script_pubkey']):
            raise ValueError('invalid public UTXO script or keychain')
        for key in ('coinbase','mature','trusted','reserved','transaction_eligible','confirmed_fee_eligible'):
            if type(row[key]) is not bool:raise ValueError('invalid public UTXO boolean')
        height=row['confirmed_height_upper_bound'];bounded_integer(height,wallet['tip_height'],True)
        public_hash(row['confirming_descendant_txid'],True)
        for key in ('confirmation_time','unconfirmed_first_seen','unconfirmed_last_seen'):bounded_integer(row[key],2**64-1,True)
        if height is None:
            if row['confirmations']!=0 or any(row[key] is not None for key in ('confirmation_block_hash','confirming_descendant_txid','confirmation_time')):
                raise ValueError('invalid unconfirmed wallet position')
        else:
            public_hash(row['confirmation_block_hash'])
            if row['confirmation_time'] is None or row['confirmations']!=wallet['tip_height']-height+1 or row['unconfirmed_first_seen'] is not None or row['unconfirmed_last_seen'] is not None:
                raise ValueError('invalid confirmed wallet position')
        mature=not row['coinbase'] or row['confirmations']>=100
        eligible=row['trusted'] and mature and not row['reserved']
        if row['reserved']!=(row['outpoint'] in reserved) or row['mature']!=mature or row['transaction_eligible']!=eligible or row['confirmed_fee_eligible']!=(eligible and height is not None):
            raise ValueError('inconsistent public wallet eligibility')
    return value


def owning_wallet_inventory(owner,network):
    service,container,address=WALLET_SOURCES[owner];started=time.time()
    frame={'service':service,'observed_at':started,'completed_at':None,'available':False,
        'source':None,'descriptor_public_canonical_checked':False,'missing_source':None}
    try:
        raw=run(['sudo','-n','docker','exec',container,'captaind','rpc','--addr',address,'wallet-inventory'],timeout=4)
        if len(raw.encode())>MAX_BYTES:raise ValueError('owning-wallet byte budget exceeded')
        source=validate_wallet_frame(json.loads(raw),owner,network)
        descriptor=source[owner]['public_descriptor']
        details=json.loads(ops('cli','getdescriptorinfo',descriptor))
        if not isinstance(details,dict) or details.get('hasprivatekeys') is not False or details.get('isrange') is not True or details.get('descriptor')!=descriptor:
            raise ValueError('canonical public ranged descriptor required')
        frame.update(available=True,source=source,descriptor_public_canonical_checked=True)
    except (ValueError,TypeError,KeyError,subprocess.SubprocessError,UnicodeError,OSError):
        # Do not echo failed child output, credentials or detailed RPC errors.
        frame['missing_source']='fixed_'+service+'_wallet_inventory_unavailable_unsupported_or_invalid'
    frame['completed_at']=time.time()
    return frame


def runtime_wallet_inventory(network):
    frames={owner:owning_wallet_inventory(owner,network) for owner in ('rounds','watchman')}
    return {'frames':frames,'separate_frames':True,'globally_atomic':False,
        'owning_sources_available':all(frame['available'] for frame in frames.values()),
        'complete':False,'qualified':False,
        'validation_scope':'bounded original public owning-wallet sources collected separately; syntax/trust/lock flags are not global financial authorization or globally atomic treasury proof'}

def _financial_readiness():
    started=time.time();before=json.loads(ops('cli','getblockchaininfo'))
    genesis=ops('cli','getblockhash','0').strip().strip('"')
    if before.get('chain')!='signet':raise ValueError('financial observer is signet only')
    configs={service:disk_financial_config(service) for service in ('captaind','watchmand')}
    images={}
    for service in ('captaind','watchmand'):
        container='abandon-signet-fallback-fb-'+service+'-1'
        images[service]=json.loads(run(['sudo','-n','docker','inspect','--format',
            '{"image_digest":{{json .Image}},"status":{{json .State.Status}},"restarts":{{json .RestartCount}}}',container]))
    declarations=public_wallet_declarations(genesis);candidates=claimant_inventory()
    wallets=runtime_wallet_inventory(before['chain'])
    ending=json.loads(ops('cli','getblockchaininfo'))
    stable=before['bestblockhash']==ending['bestblockhash']
    gaps=['deployed_product_commit_and_sdk_driver_schema_pins',
        'resolved_claimant_and_physical_backing_membership',
        'loaded_runtime_configuration_identity_and_enforced_path_caps',
        'epoch_budget_and_inflight_capital_consumption',
        'lightning_full_owned_channel_balances_and_selected_route_readiness']
    if not wallets['owning_sources_available']:gaps.append('runtime_wallet_inventory_with_canonical_positions_and_active_input_locks')
    for owner,frame in wallets['frames'].items():
        if not frame['available']:gaps.append(frame['missing_source'])
        elif (frame['source'][owner]['tip_height'],frame['source'][owner]['tip_hash'])!=(ending['blocks'],ending['bestblockhash']):
            gaps.append(owner+'_wallet_source_tip_not_current')
    if wallets['owning_sources_available'] and wallets['frames']['rounds']['source']['rounds']['public_descriptor']==wallets['frames']['watchman']['source']['watchman']['public_descriptor']:
        gaps.append('owning_wallet_sources_duplicate_descriptor')
    if not declarations.get('wallets_declared_complete'):gaps.append('public_rounds_and_watchman_wallet_declarations')
    if candidates['truncated_tables']:gaps.append('bounded_candidate_inventory_truncated')
    if not stable:gaps.append('chain_tip_changed')
    if ending.get('initialblockdownload') is not False:gaps.append('chain_not_synchronized')
    return {'kind':'financial_readiness','observed_at':started,'completed_at':time.time(),
        'network':ending['chain'],'genesis':genesis,'height':ending['blocks'],'tip_hash':ending['bestblockhash'],
        'stable_tip':stable,'images':images,'disk_config':configs,'wallet_declarations':declarations,
        'candidate_inventory':candidates,
        'runtime_wallet_inventory':wallets,'effective_fee_ceilings_sat_kvb':None,
        'capital_exposure_msat':None,'available_fee_funding_msat':None,
        'budget_remaining_msat':None,'pool_ready':None,'channel_route_ready':None,
        'complete':False,'qualified':False,'gaps':gaps,
        'validation_scope':'bounded original read-only facts; missing monetary producers hold dispatch; disk estimator ceilings are not effective transaction-path limits'}

def financial_readiness():
    global READ_DEADLINE
    previous=READ_DEADLINE;READ_DEADLINE=time.monotonic()+20
    try:return _financial_readiness()
    finally:READ_DEADLINE=previous

def collect(kind,after):
    if kind=='claimant_inventory':return claimant_inventory()
    if kind=='financial_readiness':return financial_readiness()
    if kind=='logs':return logs(after)
    if kind=='rounds_descriptor':return {'kind':kind,'declaration':json.loads(DESCRIPTOR.read_text())}
    if kind=='binding_check':
        def byte_array(items):return 'ARRAY['+','.join("decode('"+item+"','hex')" for item in items)+']::bytea[]'
        mailboxes=byte_array(after['mailboxes']);keys=byte_array(after['keys'])
        sql=("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='8s'; SELECT jsonb_build_object("
            "'records',(SELECT coalesce(jsonb_agg(q),'[]'::jsonb) FROM (SELECT encode(mailbox_pk,'hex') AS mailbox_pk,encode(spk,'hex') AS spk,encode(account_xpub,'hex') AS account_xpub,encode(mailbox_sig,'hex') AS mailbox_sig,encode(account_sig,'hex') AS account_sig FROM fallback_record WHERE mailbox_pk=ANY("+mailboxes+")) q),"
            "'links',(SELECT coalesce(jsonb_agg(q),'[]'::jsonb) FROM (SELECT encode(user_pubkey,'hex') AS user_pubkey,encode(mailbox_pk,'hex') AS mailbox_pk,child_index FROM key_link WHERE user_pubkey=ANY("+keys+")) q)); COMMIT;")
        lines=[line for line in ops('sql',sql).splitlines() if line.startswith('{')]
        if len(lines)!=1:raise ValueError('invalid binding response')
        result=json.loads(lines[0])
        return {'kind':kind,'fallback_protocol':3,'mailboxes':after['mailboxes'],'keys':after['keys'],**result,'inventory_consistent':True,
            'validation_scope':'one bounded read-only database snapshot of the requested public records and links'}
    if kind!='snapshot':return page(kind,after)
    before=json.loads(ops('cli','getblockchaininfo'))
    image=json.loads(run(['sudo','-n','docker','inspect','--format','{"image_digest":{{json .Image}},"status":{{json .State.Status}},"restarts":{{json .RestartCount}}}',CONTAINER]))
    cfg=tomllib.loads((DEPLOY/'state/signet-fallback/captaind.toml').read_text())
    ending=json.loads(ops('cli','getblockchaininfo'))
    return {'kind':kind,'observed_at':time.time(),'height':ending['blocks'],'tip_hash':ending['bestblockhash'],
        'network':ending['chain'],'genesis':ops('cli','getblockhash','0').strip().strip('"'),
        'stable_tip':before['bestblockhash']==ending['bestblockhash'],'initial_block_download':ending['initialblockdownload'],
        'container':CONTAINER,'server_status':image,'wallet':None,
        'wallet_inventory_complete':False,
        'wallet_scope':'not collected: legacy wallet-status RPC reveals an address; read-only runtime signer/lock inventory is required',
        'timings':{key:cfg.get('expiry_payout',{}).get(key) for key in ['enabled','min_payout_sat','grace_blocks','sweep_min_confs']},
        'lightning_configured':bool(cfg.get('cln_array')),'complete':False}

def main():
    kind,after=request(sys.stdin.buffer.read(4097));result=collect(kind,after)
    encoded=json.dumps(result,allow_nan=False)
    if len(encoded.encode())>MAX_BYTES:raise ValueError('serialized response too large')
    print(encoded)
if __name__=='__main__':
    try:main()
    except Exception:
        print('read-only server observation unavailable',file=sys.stderr);sys.exit(1)
