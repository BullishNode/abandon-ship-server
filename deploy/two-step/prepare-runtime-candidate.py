#!/usr/bin/python3
"""Stage four source-bound binaries in a candidate image. Never deploy services."""
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import uuid

BASE = 'sha256:089396ec2da23eb35e6cfe0477bc505a21c622c9bbd1e7230dcc2e6115cffe68'
BINS = ('captaind', 'watchmand', 'bark', 'barkd')
FLOOR = 42 * 1024**3
EMPTY = hashlib.sha256(b'').hexdigest()


def sha(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def run(argv, directory, label, timeout=180):
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with (directory / (label + '.stdout')).open('xb') as out, (directory / (label + '.stderr')).open('xb') as err:
        result = subprocess.run(argv, stdout=out, stderr=err, timeout=timeout)
    receipt = {'argv': argv, 'started_utc': started,
               'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'exit': result.returncode,
               'stdout_sha256': sha(directory / (label + '.stdout')),
               'stderr_sha256': sha(directory / (label + '.stderr'))}
    (directory / (label + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError('candidate command failed; preserve ' + label)
    return (directory / (label + '.stdout')).read_text()


def main():
    if len(sys.argv) != 3:
        raise ValueError('provide exact declaration file and its SHA256')
    declaration_path = pathlib.Path(sys.argv[1])
    raw = declaration_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
        raise ValueError('declaration bytes differ')
    declaration = json.loads(raw)
    if set(declaration) != {'head', 'tree', 'archive', 'archive_sha256', 'artifact_metadata_sha256'}:
        raise ValueError('exact source/archive declaration required')
    if any(not re.fullmatch('[0-9a-f]{40}', declaration[k]) for k in ('head', 'tree')):
        raise ValueError('immutable source commit and tree required')
    archive = pathlib.Path(declaration['archive'])
    if (not archive.is_absolute() or archive.is_symlink() or not archive.is_file()
            or archive.stat().st_size > 1024**3 or sha(archive) != declaration['archive_sha256']):
        raise ValueError('exact bounded native artifact archive required')
    if shutil.disk_usage('/').free < FLOOR:
        raise ValueError('owned target below storage floor')
    directory = pathlib.Path('/var/tmp') / ('a-candidate-runtime-' + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    (directory / 'DECLARATION.json').write_bytes(raw)
    context = directory / 'context'
    context.mkdir(mode=0o700)
    before = run(['sudo', '-n', 'docker', 'ps', '--no-trunc', '--format', '{{.ID}} {{.Image}} {{.Status}}'],
                 directory, 'protective-services-before')
    protected_ids = [line.split()[0] for line in before.splitlines()]
    if not protected_ids:
        raise ValueError('protective services missing before packaging')
    state_format = '{{.Id}} {{.Image}} {{.State.Pid}} {{.State.StartedAt}} {{.State.Running}}'
    protected_before = run(['sudo', '-n', 'docker', 'inspect', '--format', state_format] + protected_ids,
                           directory, 'protective-processes-before')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        expected = {'candidate-artifact.json'} | {'candidate-artifacts/' + name for name in BINS}
        if (len(members) != len(expected) or {m.name for m in members} != expected
                or any(not m.isfile() or m.issym() or m.islnk() for m in members)):
            raise ValueError('archive contains an unexpected native artifact')
        original = tar.extractfile('candidate-artifact.json').read(1024**2 + 1)
        if (len(original) > 1024**2 or hashlib.sha256(original).hexdigest()
                != declaration['artifact_metadata_sha256']):
            raise ValueError('original build receipt differs')
        metadata = json.loads(original)
        artifacts = metadata['candidate_daemons']
        if set(artifacts) != set(BINS) or metadata.get('mixed_provenance') is not False:
            raise ValueError('four coherent native binaries required')
        for name in BINS:
            artifact = artifacts[name]
            identity = artifact['source']
            if (identity['head'] != declaration['head'] or identity['tracked_delta_sha256'] != EMPTY
                    or identity['untracked_files_sha256'] or declaration['head'] not in artifact['native_version']):
                raise ValueError('native executable source identity differs')
            member = tar.getmember('candidate-artifacts/' + name)
            if member.size != artifact['bytes'] or not 0 < member.size <= 160 * 1024**2:
                raise ValueError('native executable size differs')
            with tar.extractfile(member) as stream, (context / name).open('xb') as output:
                shutil.copyfileobj(stream, output, length=1024**2)
            if sha(context / name) != artifact['sha256']:
                raise ValueError('native executable bytes differ')
            (context / name).chmod(0o755)
    source_manifest = {
        'status': 'CANDIDATE_NOT_DEPLOYED_NOT_RELEASE_QUALIFIED',
        'native_commit': declaration['head'], 'native_tree': declaration['tree'],
        'base_image': BASE, 'build_receipt_sha256': declaration['artifact_metadata_sha256'],
        'binaries': {n: {'sha256': artifacts[n]['sha256'], 'version': artifacts[n]['native_version']} for n in BINS},
        'embedded_web_distribution': None,
        'web_submodule_build_provenance_only': artifacts['barkd']['source']['web_head'],
        'scope': 'Native compilation unset BARK_WEB_DIST. API executable only; no embedded UI readiness claim.'}
    (context / 'SOURCE_MANIFEST.json').write_text(json.dumps(source_manifest, sort_keys=True, indent=2) + '\n')
    dockerfile = 'FROM ' + BASE + '\n'
    dockerfile += ''.join('COPY ' + name + ' /app/' + name + '\n' for name in BINS)
    dockerfile += 'COPY SOURCE_MANIFEST.json /app/SOURCE_MANIFEST.json\nUSER 2001:2001\n'
    (context / 'Dockerfile').write_text(dockerfile)
    tag = 'two-step-a-candidate:' + declaration['head'][:12] + '-' + directory.name[-12:]
    run(['sudo', '-n', 'docker', 'build', '--pull=false', '--network=none', '--tag', tag, str(context)],
        directory, 'candidate-image-build', timeout=180)
    image = run(['sudo', '-n', 'docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                directory, 'candidate-image-id').strip()
    if not re.fullmatch('sha256:[0-9a-f]{64}', image):
        raise ValueError('actual image identity unavailable')
    sandbox = ['sudo', '-n', 'docker', 'run', '--rm', '--network=none', '--read-only', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=256m', '--cpus=1']
    hashes = run(sandbox + ['--entrypoint', '/usr/bin/sha256sum', image] + ['/app/' + n for n in BINS],
                 directory, 'image-executable-hashes')
    if {line.split()[1]: line.split()[0] for line in hashes.splitlines()} != {
            '/app/' + n: artifacts[n]['sha256'] for n in BINS}:
        raise ValueError('actual image executable identities differ')
    for name in BINS:
        version = run(sandbox + ['--entrypoint', '/app/' + name, image, '--version'],
                      directory, 'image-version-' + name).strip()
        if version != artifacts[name]['native_version']:
            raise ValueError('actual image executable version differs')
    after = run(['sudo', '-n', 'docker', 'ps', '--no-trunc', '--format', '{{.ID}} {{.Image}} {{.Status}}'],
                directory, 'protective-services-after')
    identities = lambda value: sorted(line.split()[:2] for line in value.splitlines())
    if identities(before) != identities(after):
        raise ValueError('protective container/image set changed during packaging')
    protected_after = run(['sudo', '-n', 'docker', 'inspect', '--format', state_format] + protected_ids,
                          directory, 'protective-processes-after')
    if (protected_before != protected_after
            or any(line.split()[-1] != 'true' for line in protected_after.splitlines())):
        raise ValueError('protective process identity changed during packaging')
    if shutil.disk_usage('/').free < FLOOR:
        raise ValueError('post-build owned target storage floor violated')
    result = {'status': 'IMAGE_PACKAGED_NOT_DEPLOYED_NOT_RELEASE_QUALIFIED', 'image': image, 'tag': tag,
              'source': declaration['head'], 'tree': declaration['tree'], 'evidence': str(directory),
              'source_manifest_sha256': sha(context / 'SOURCE_MANIFEST.json'),
              'services_preserved': True, 'network_or_wallet_access': False}
    (directory / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    os.umask(0o077)
    main()
