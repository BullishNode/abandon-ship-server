#!/usr/bin/env python3
"""Prepare a native-compatible witness directory; never install or overwrite it.

This formats a previously exported DB inventory, not a proof of complete history.
Before installation, freeze writers, reconcile confirmed AND mempool outflows,
check the final descriptor/range and DB snapshot, and retain independent backups.
The shipping daemon must validate the result before its workers can start.
"""
import argparse
import json
import os
from pathlib import Path
import re


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode()


def prepare(inventory, core_genesis, server_pubkey, output):
    if not re.fullmatch(r'[0-9a-f]{64}', core_genesis):
        raise ValueError('invalid Core genesis hash')
    if not re.fullmatch(r'0[23][0-9a-f]{64}', server_pubkey):
        raise ValueError('invalid compressed server public key')
    records = {}
    paid_ids = set()
    for row in inventory['nursery']:
        if row['kind'] != 'expiry-payout':
            continue
        txid = row['txid']
        if not re.fullmatch(r'[0-9a-f]{64}', txid) or txid in records:
            raise ValueError('invalid or duplicate transaction id')
        raw = bytes.fromhex(row['raw_hex'])
        if not raw:
            raise ValueError('empty raw transaction')
        settlements = []
        for item in row['settlements'] or []:
            coin = item['id']
            if not re.fullmatch(r'[0-9a-f]{64}:[0-9]+', coin) or coin in paid_ids:
                raise ValueError('invalid or duplicate settled coin')
            fee = item['fee_sat']
            if type(fee) is not int or not 0 <= fee <= 2**63 - 1:
                raise ValueError('invalid settlement fee')
            script = item['spk_hex']
            settlements.append({'id': coin,
                'spk': None if script is None else list(bytes.fromhex(script)),
                'fee_sat': fee})
            paid_ids.add(coin)
        if not settlements:
            raise ValueError('payout has no settlements')
        settlements.sort(key=lambda item: item['id'])
        records[txid] = {'tx': list(raw), 'settlements': settlements}
    if not records:
        raise ValueError('existing-stack bootstrap must not produce empty history')
    # ChainHash::to_string displays raw hash bytes, unlike Core's blockhash RPC.
    identity = {'version': 1, 'genesis': bytes.fromhex(core_genesis)[::-1].hex(),
        'server_pubkey': server_pubkey}
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(output, 0o700)
    files = {'identity.json': compact(identity),
        'history': ''.join(txid + '\n' for txid in sorted(records)).encode()}
    files.update({txid + '.json': compact(record) for txid, record in records.items()})
    for name, content in files.items():
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    for directory in [output, output.parent]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {'result': 'PREPARED_NOT_INSTALLED', 'payouts': len(records),
        'settlements': len(paid_ids), 'directory': str(output),
        'scope': 'format conversion only; native startup and frozen complete-history reconciliation required'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--core-genesis', required=True)
    parser.add_argument('--server-pubkey', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(prepare(json.loads(args.inventory.read_text()),
        args.core_genesis, args.server_pubkey, args.output)))
