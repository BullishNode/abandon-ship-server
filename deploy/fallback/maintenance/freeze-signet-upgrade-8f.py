"""Freeze ONLY the owned fallback signet stack after simulator coordination.

Run on the target VM. Never resets state, installs a witness, or starts a daemon.
Backups containing keys remain on the VM. Any failed post-stop check leaves the
stack stopped for inspection; never automatically restore an older database.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import subprocess

HEAD = '8f2286ee7e1631b0ceccd1631ec3b728a1bb0a52'
ROOT = Path.home() / 'abandon-signet-fallback'
DEPLOY = ROOT / 'deploy'
SERVICES = ['fb-web', 'fb-web2', 'fb-api', 'fb-api2', 'fb-barkd', 'fb-barkd2',
            'fb-ark', 'fb-watchmand', 'fb-captaind']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordination', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-pending-payout', action='store_true',
                        help='require actual pending work to qualify the in-flight upgrade scenario')
    args = parser.parse_args()
    coordinated = json.loads(args.coordination.read_text())
    assert coordinated['simulator_dispatch_paused'] is True
    assert coordinated['inflight_actions'] == coordinated['unknown_actions'] == 0
    assert coordinated['recovery_inflight_or_unknown'] == 0, 'global recovery uncertainty must be reconciled before freezing'
    assert coordinated['target_head'] == HEAD
    assert coordinated['target_vm'] == '172.81.182.157'
    assert coordinated.get('observed_utc'), 'coordination must carry a fresh observation'
    observed = datetime.datetime.fromisoformat(coordinated['observed_utc'])
    assert observed.tzinfo is not None
    age = (datetime.datetime.now(datetime.timezone.utc) - observed).total_seconds()
    assert 0 <= age < 600, 'reverify simulator pause instead of trusting stale evidence'
    expected_pending = coordinated.get('pending_payout_txids', [])
    assert isinstance(expected_pending, list)
    if args.require_pending_payout:
        assert expected_pending, 'actual pending payouts are required for this qualification run'
    assert all(isinstance(txid, str) and len(txid) == 64 and
               all(c in '0123456789abcdef' for c in txid) for txid in expected_pending)
    assert len(expected_pending) == len(set(expected_pending))
    args.output.mkdir(mode=0o700, exist_ok=False)
    os.chdir(DEPLOY)
    dcf = ['sudo', './fallback/dcf']

    def run(command):
        return subprocess.check_output(command, text=True).strip()

    def nursery_snapshot():
        sql = """BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT COALESCE(jsonb_agg(jsonb_build_object(
 'txid',n.txid,'kind',n.kind::text,'raw_hex',encode(n.tx,'hex'),
 'confirmed_at_height',n.confirmed_at_height,'abandoned_at',n.abandoned_at,
 'settlements',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
   'id',s.id,'spk_hex',encode(s.spk,'hex'),'fee_sat',s.fee_sat) ORDER BY s.id),'[]'::jsonb)
   FROM expiry_settlement s WHERE s.txid=n.txid)) ORDER BY n.txid),'[]'::jsonb)
FROM nursery_tx n;
COMMIT;"""
        lines = run(['./fallback/ops.sh', 'sql', sql]).splitlines()
        rows = [json.loads(line) for line in lines if line.startswith('[')]
        assert len(rows) == 1
        return {row['txid']: row for row in rows[0]}

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, indent=2) + '\n')

    def inspect(service):
        return json.loads(run(['sudo', 'docker', 'inspect',
                              'abandon-signet-fallback-' + service + '-1']))[0]

    assert json.loads(run(['./fallback/ops.sh', 'cli', 'getblockchaininfo']))['chain'] == 'signet'
    images = json.loads((ROOT / 'release-prep/8f2286ee7-1/images.json').read_text())
    for record in images.values():
        actual = json.loads(run(['sudo', 'docker', 'image', 'inspect', record['tag']]))[0]
        assert actual['Id'] == record['id']
        assert actual['Config']['Labels']['org.opencontainers.image.revision'] == HEAD
    states = {service: inspect(service) for service in SERVICES}
    assert states['fb-captaind']['State']['Running'] and states['fb-watchmand']['State']['Running']
    assert states['fb-captaind']['Image'] == coordinated['target_identity']['image_digest'], \
        'target changed since the simulator pause identity was declared'
    pre_nursery = nursery_snapshot()
    for txid in expected_pending:
        row = pre_nursery[txid]
        assert row['kind'] == 'expiry-payout' and row['settlements']
        assert row['confirmed_at_height'] is None and row['abandoned_at'] is None, \
            'declared pending payout has changed; obtain fresh coordination evidence'
    save('pre-stop-nursery.json', list(pre_nursery.values()))
    before = {service: {'running': state['State']['Running'], 'pid': state['State']['Pid'],
                       'image': state['Image']} for service, state in states.items()}
    save('before-services.json', before)
    override = DEPLOY / 'state/signet-fallback/simulation.env'
    assert override.is_file(), 'keep the installed simulation topology and pool settings'
    override_hash = hashlib.sha256(override.read_bytes()).hexdigest()
    (args.output / 'public-env-before').write_bytes((DEPLOY / 'fallback/signet-fallback.env').read_bytes())
    info = json.loads(run(['./fallback/ops.sh', 'barkd', 'GET', 'wallet/ark-info']))
    assert info['network'] == 'signet'
    assert info['server_pubkey'] == coordinated['target_identity']['server_pubkey']
    save('public-identity.json', {'network': info['network'], 'server_pubkey': info['server_pubkey'],
                                'genesis': run(['./fallback/ops.sh', 'cli', 'getblockhash', '0'])})

    def backup(name):
        path = args.output / name
        with path.open('xb') as stream:
            subprocess.run(dcf + ['exec', '-T', 'fb-postgres', 'pg_dump', '-U', 'postgres',
                '-d', 'bark-server-db', '--format=custom'], stdout=stream, check=True)
        with path.open('rb') as stream:
            subprocess.run(dcf + ['exec', '-T', 'fb-postgres', 'pg_restore', '--list'],
                           stdin=stream, stdout=subprocess.DEVNULL, check=True)
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'scope': 'custom dump with readable archive table of contents; full restore audit separately required'}

    initial = backup('pre-stop.dump')
    subprocess.run(dcf + ['stop'] + SERVICES, check=True)
    for service, state in before.items():
        assert not inspect(service)['State']['Running']
        assert inspect(service)['State']['Pid'] == 0
        assert state['pid'] == 0 or not Path('/proc', str(state['pid'])).exists()
    # Wait for any detached prior COMMIT before the frozen inventory/dump.
    run(['./fallback/ops.sh', 'sql',
         "BEGIN; SET LOCAL statement_timeout='180s'; LOCK TABLE nursery_tx IN SHARE MODE; COMMIT;"])
    frozen_nursery = nursery_snapshot()
    # Confirmation during the stop is allowed; losing a signed transaction or
    # changing its economic identity is not. Preserve new raced commits too.
    for txid, row in pre_nursery.items():
        retained = frozen_nursery[txid]
        assert all(retained[key] == row[key] for key in ('kind', 'raw_hex', 'settlements')), \
            'nursery transaction or recipient accounting changed; leave stopped for inspection'
    save('frozen-nursery.json', list(frozen_nursery.values()))
    frozen = backup('frozen.dump')
    volumes = {}
    for service, destination in [('fb-captaind', '/data/captaind'),
                                 ('fb-watchmand', '/data/watchmand'),
                                 ('fb-barkd', '/data'), ('fb-barkd2', '/data')]:
        matches = [mount for mount in states[service]['Mounts'] if mount['Destination'] == destination]
        assert len(matches) == 1 and matches[0]['Type'] == 'volume'
        mount = matches[0]
        path = args.output / (service + '-volume.tar')
        with path.open('xb') as stream:
            subprocess.run(['sudo', 'tar', '-C', mount['Source'], '-cpf', '-', '.'],
                           stdout=stream, check=True)
        with path.open('rb') as stream:
            subprocess.run(['tar', '-tf', '-'], stdin=stream, stdout=subprocess.DEVNULL, check=True)
        volumes[service] = {'source': mount['Source'], 'archive': str(path),
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    assert hashlib.sha256(override.read_bytes()).hexdigest() == override_hash
    save('freeze-result.json', {'result': 'FROZEN_BACKED_UP_NOT_UPGRADED', 'head': HEAD,
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'before_services': before, 'declared_pending_payouts': expected_pending,
        'inflight_upgrade_scenario_started': bool(expected_pending),
        'inflight_upgrade_scenario_complete': False,
        'pending_nursery_at_freeze': sum(row['confirmed_at_height'] is None and row['abandoned_at'] is None
                                        for row in frozen_nursery.values()),
        'retained_pre_stop_transactions': len(pre_nursery),
        'new_transactions_during_stop': sorted(set(frozen_nursery) - set(pre_nursery)),
        'backups': [initial, frozen],
        'volumes': volumes, 'simulation_override_sha256': override_hash,
        'coordination_sha256': hashlib.sha256(args.coordination.read_bytes()).hexdigest(),
        'next': 'frozen history/mempool and descriptor reconciliation, full dump restore audit, '
                'witness formatting and independent backup, native startup validation, exact prior services only'})
    print('Owned fallback stack frozen and backed up; not upgraded. Private archives stay on this VM.')


if __name__ == '__main__':
    main()
