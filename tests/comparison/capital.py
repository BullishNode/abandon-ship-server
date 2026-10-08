#!/usr/bin/env python3
"""Reconcile a bounded transaction graph with an independently captured UTXO set."""
import argparse
import json
from pathlib import Path
from oracle import sat


def verify(evidence):
    transactions = evidence['transactions']
    opening = evidence['opening']
    external = dict(evidence.get('external_inputs', {}))
    assert not (opening.keys() & external.keys()), 'opening input also declared external'
    live = dict(opening)
    initial = sum(row['amount_sat'] for row in opening.values())
    inflow = 0
    fees = {}
    for outpoint, row in {**opening, **external}.items():
        txid, index = outpoint.rsplit(':', 1)
        amount = sat(transactions[txid]['vout'][int(index)]['value'])
        assert amount == row['amount_sat'], f'opening/external amount differs from Core: {outpoint}'
        assert amount >= 0
    steps = evidence['steps']
    assert len(steps) == len(set(steps)), 'transaction counted twice'
    for txid in steps:
        tx = transactions[txid]
        assert tx['txid'] == txid, 'transaction identity mismatch'
        assert tx.get('confirmations', 0) >= 0, 'conflicted transaction in capital graph'
        input_sat = 0
        for item in tx['vin']:
            assert 'coinbase' not in item, 'declare coinbase funding as external input'
            outpoint = f"{item['txid']}:{item['vout']}"
            if outpoint in external:
                row = external.pop(outpoint)
                inflow += row['amount_sat']
            else:
                assert outpoint in live, f'unrecorded funding, missing predecessor or repeated input: {outpoint}'
                row = live.pop(outpoint)
            input_sat += row['amount_sat']
        output_sat = 0
        for index, output in enumerate(tx['vout']):
            value = sat(output['value'])
            assert value >= 0
            output_sat += value
            script = output['scriptPubKey']['hex']
            if script.startswith('6a'):
                assert value == 0, 'nonzero unspendable output needs explicit burn accounting'
                continue
            outpoint = f'{txid}:{index}'
            assert outpoint not in live
            bucket = evidence['output_buckets'][outpoint]
            assert bucket in ['rounds', 'watchman', 'payout', 'tree', 'user', 'external'], 'unknown value owner'
            live[outpoint] = dict(amount_sat=value, bucket=bucket)
        fee = input_sat - output_sat
        assert fee >= 0, 'negative transaction fee'
        fees[txid] = fee
    assert not external, 'declared external funding was not used'
    # These facts must come from Core gettxout, not from the implementation's
    # balance/paid counters or the same transaction-graph calculation.
    observed = evidence['unspent']
    assert live.keys() == observed.keys(), 'closing UTXO set differs: missing spend or unexpected funds'
    buckets = {}
    for outpoint, row in live.items():
        assert row['amount_sat'] == observed[outpoint], f'closing amount differs from Core: {outpoint}'
        buckets[row['bucket']] = buckets.get(row['bucket'], 0) + row['amount_sat']
    closing = sum(observed.values())
    assert initial + inflow == closing + sum(fees.values()), 'value is not conserved'
    for bucket, floor in evidence.get('closing_min_sat', {}).items():
        assert buckets.get(bucket, 0) >= floor, f'{bucket} capital below required floor'
    return dict(opening_sat=initial, external_inflow_sat=inflow, closing_sat=closing,
                closing_buckets_sat=buckets, mining_fees_sat=fees,
                scope='declared transaction graph; not a complete wallet or liability audit')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.evidence.read_text())), indent=2))
