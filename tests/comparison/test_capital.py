"""Synthetic counterexamples for the capital oracle, not funded-cycle evidence."""
import unittest
from capital import verify


def facts():
    return dict(opening={'fund:0': dict(amount_sat=10000, bucket='rounds')},
        transactions={
            'fund': dict(vout=[dict(value='0.00010000')]),
            'payout': dict(txid='payout', vin=[dict(txid='fund',vout=0)], vout=[
                dict(value='0.00007900',scriptPubKey=dict(hex='51')),
                dict(value='0.00002000',scriptPubKey=dict(hex='52'))]),
            'spend': dict(txid='spend',vin=[dict(txid='payout',vout=0)],vout=[
                dict(value='0.00007800',scriptPubKey=dict(hex='53'))])},
        steps=['payout','spend'], output_buckets={'payout:0':'user','payout:1':'rounds','spend:0':'user'},
        unspent={'payout:1':2000,'spend:0':7800})


class CapitalSensitivity(unittest.TestCase):
    def test_internal_hop_counted_once(self):
        result=verify(facts())
        self.assertEqual(result['closing_sat'],9800)
        self.assertEqual(result['mining_fees_sat'],{'payout':100,'spend':100})
        self.assertEqual(result['closing_buckets_sat'],{'rounds':2000,'user':7800})

    def test_detects_hidden_topup(self):
        data=facts()
        data['transactions']['payout']['vin'].append(dict(txid='faucet',vout=0))
        with self.assertRaisesRegex(AssertionError,'unrecorded funding'):
            verify(data)
        data['transactions']['faucet']=dict(vout=[dict(value='0.00001000')])
        data['external_inputs']={'faucet:0':dict(amount_sat=1000,bucket='external')}
        self.assertEqual(verify(data)['external_inflow_sat'],1000)

    def test_actual_unspent_set_and_values_required(self):
        for mutation, message in [
            (lambda d:d['unspent'].pop('spend:0'),'UTXO set differs'),
            (lambda d:d['unspent'].update({'spend:0':7900}),'amount differs'),
            (lambda d:d['steps'].pop(),'UTXO set differs'),
        ]:
            with self.subTest(message=message):
                data=facts();mutation(data)
                with self.assertRaisesRegex(AssertionError,message): verify(data)

    def test_duplicate_input_or_tx_rejected(self):
        data=facts();data['steps'].append('spend')
        with self.assertRaisesRegex(AssertionError,'counted twice'): verify(data)
        data=facts();data['transactions']['spend']['vin'].append(dict(txid='payout',vout=0))
        with self.assertRaisesRegex(AssertionError,'repeated input'): verify(data)

    def test_initial_value_checked_against_chain(self):
        data=facts();data['opening']['fund:0']['amount_sat']+=1
        with self.assertRaisesRegex(AssertionError,'amount differs from Core'): verify(data)

    def test_operator_capital_does_not_hide_in_payout_wallet(self):
        data=facts();data['output_buckets']['payout:1']='payout'
        data['closing_min_sat']={'rounds':2000}
        with self.assertRaisesRegex(AssertionError,'rounds capital below'): verify(data)


if __name__ == '__main__': unittest.main()
