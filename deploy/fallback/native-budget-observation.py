#!/usr/bin/env python3
"""Fixed public disk projection; never loads/locks/initializes a native budget."""
import hashlib,json,os,re,stat,sys,time
from pathlib import Path

DIRECTORY=Path('/var/lib/docker/volumes/abandon-signet-fallback_captaind/_data/direct-expiry-witness')
LIMITS={'ordinary-fee-policy.json':2048,'ordinary-fee-opening.json':2048,'ordinary-fee-budget':2*1024*1024}
MAX_MONEY=21_000_000*100_000_000


def amount(value,positive=False):
    if type(value) is not int or not int(positive)<=value<=MAX_MONEY:raise ValueError('invalid public native budget amount')
    return value


def digest(value,nullable=False):
    if nullable and value is None:return value
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('invalid native budget digest')
    return value


def canonical(raw,keys):
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!=set(keys):raise ValueError('invalid native budget fields')
    if json.dumps({key:value[key] for key in keys},separators=(',',':'),allow_nan=False).encode()!=raw:
        raise ValueError('noncanonical native budget metadata')
    return value


def project(files):
    if set(files)!=set(LIMITS) or any(type(files[name])is not bytes or len(files[name])>limit for name,limit in LIMITS.items()):
        raise ValueError('native budget source bounds')
    policy_raw,opening_raw,history=(files[name] for name in LIMITS)
    stored=canonical(policy_raw,('policy','opening_sha256'))
    policy=stored['policy'];policy_keys=('urgent_reserve_sat','ordinary_budget_sat','external_future_reserved_sat','opening_sha256')
    if not isinstance(policy,dict) or list(policy)!=list(policy_keys):raise ValueError('invalid native policy fields')
    for key in policy_keys[:3]:amount(policy[key],key!='external_future_reserved_sat')
    if policy['external_future_reserved_sat']>policy['ordinary_budget_sat']:raise ValueError('external partition exceeds budget')
    adopted=digest(policy['opening_sha256'],True)
    if adopted=='0'*64:raise ValueError('zero adoption digest')
    opening_sha=hashlib.sha256(opening_raw).hexdigest()
    if digest(stored['opening_sha256'])!=opening_sha or adopted not in (None,opening_sha):raise ValueError('opening digest differs')
    fields=('version','network','genesis','server_pubkey','history_prefix_bytes','history_prefix_sha256','native_spent_sat','external_spent_sat','accounting_receipt_sha256')
    opening=canonical(opening_raw,fields)
    if type(opening['version']) is not int or opening['version']!=1 or opening['network']!='signet':raise ValueError('unsupported budget identity')
    digest(opening['genesis']);digest(opening['history_prefix_sha256']);receipt=digest(opening['accounting_receipt_sha256'],True)
    if not isinstance(opening['server_pubkey'],str) or not re.fullmatch('(02|03)[0-9a-f]{64}',opening['server_pubkey']):raise ValueError('invalid server identity')
    prefix=opening['history_prefix_bytes']
    if type(prefix) is not int or not 0<=prefix<=650_000 or prefix%65:raise ValueError('invalid original history prefix bound')
    native=amount(opening['native_spent_sat']);external=amount(opening['external_spent_sat'])
    if adopted is None:
        if native or external or prefix or opening['history_prefix_sha256']!=hashlib.sha256(b'').hexdigest() or receipt is not None:
            raise ValueError('fresh metadata cannot adopt old history or costs')
    elif not prefix or not native or receipt in (None,'0'*64):raise ValueError('adoption requires original history and receipt')
    initial=native+external+policy['external_future_reserved_sat']
    if initial>policy['ordinary_budget_sat']:raise ValueError('opening exceeds ordinary allowance')
    if history and not history.endswith(b'\n'):raise ValueError('torn native reservation append')
    attempts=0;reserved=0
    # Match Rust str::lines: LF or CRLF delimiters, no other byte separators.
    for line in history.split(b'\n')[:-1]:
        line=line.removesuffix(b'\r')
        if not re.fullmatch(b'[1-9][0-9]{0,15}',line):raise ValueError('noncanonical positive reservation')
        ceiling=amount(int(line),True);reserved+=ceiling;attempts+=1
        if initial+reserved>policy['ordinary_budget_sat']:raise ValueError('reservations exceed ordinary allowance')
    return {'schema_version':1,'source_scope':'public_native_disk_budget_observation',
        'policy_sha256':hashlib.sha256(policy_raw).hexdigest(),'opening_sha256':opening_sha,
        'budget_file_sha256':hashlib.sha256(history).hexdigest(),'budget_file_bytes':len(history),
        'network':opening['network'],'genesis':opening['genesis'],'server_pubkey':opening['server_pubkey'],
        'ordinary_budget_sat':policy['ordinary_budget_sat'],'urgent_reserve_sat':policy['urgent_reserve_sat'],
        'external_future_reserved_sat':policy['external_future_reserved_sat'],
        'opening_native_spent_sat':native,'opening_external_spent_sat':external,
        'history_prefix_bytes':prefix,'history_prefix_sha256':opening['history_prefix_sha256'],
        'accounting_receipt_sha256':receipt,'opening_charge_sat':initial,
        'reserved_ceiling_sum_sat':reserved,'attempt_count':attempts,'total_charged_sat':initial+reserved,
        'remaining_ordinary_allowance_sat':policy['ordinary_budget_sat']-initial-reserved,
        'runtime_loaded_identity_verified':False,'retained_signed_fee_coverage_verified':False,
        'urgent_selection_isolation_verified':False,'complete':False,'qualified':False}


def metadata(st):return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def read_directory(path):
    """Internal testable reader. CLI accepts no caller path; all opens no-follow."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('canonical fixed directory required')
    directory=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    files={};fds=[];original={}
    try:
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        for name,limit in LIMITS.items():
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory);fds.append((name,fd))
            before=os.fstat(fd)
            original[name]=metadata(before)
            if not stat.S_ISREG(before.st_mode) or before.st_size>limit:raise ValueError('native budget source bound/type')
            with os.fdopen(os.dup(fd),'rb')as stream:raw=stream.read(limit+1)
            if len(raw)!=before.st_size or metadata(before)!=metadata(os.fstat(fd)):raise ValueError('native budget changed during read')
            files[name]=raw
        value=project(files)
        for name,fd in fds:
            if original[name]!=metadata(os.fstat(fd)) or original[name]!=metadata(os.stat(name,dir_fd=directory,follow_symlinks=False)):
                raise ValueError('native budget replaced during read')
            # Some filesystems can retain timestamps across rapid same-size
            # writes. Recheck the retained descriptor bytes, not metadata alone.
            os.lseek(fd,0,os.SEEK_SET)
            with os.fdopen(os.dup(fd),'rb')as stream:rechecked=stream.read(LIMITS[name]+1)
            if rechecked!=files[name] or original[name]!=metadata(os.fstat(fd)):
                raise ValueError('native budget bytes changed during read')
        return value
    finally:
        for name,fd in fds:os.close(fd)
        os.close(directory)


def main():
    if len(sys.argv)!=1:raise ValueError('fixed observation accepts no arguments')
    started=time.time();value=read_directory(DIRECTORY)
    value.update(observed_at=started,completed_at=time.time())
    encoded=json.dumps(value,allow_nan=False)
    if len(encoded.encode())>16384:raise ValueError('native public projection bound')
    print(encoded)


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,UnicodeError,TypeError,KeyError):
        print('native budget disk observation unavailable',file=sys.stderr);sys.exit(2)
