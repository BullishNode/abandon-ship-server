"""Sensitivity checks for the oracle; these do not replace real-node scenarios."""
from copy import deepcopy
import unittest

from oracle import verify


def evidence():
    return {
        "max_fee_pct": 20,
        "coins": {
            "root:0": {"amount_sat": 70000, "parents": []},
            "a:0": {"amount_sat": 40000, "payout_script": "51", "parents": ["root:0"]},
            "b:0": {"amount_sat": 30000, "payout_script": "51", "parents": ["root:0"]},
            "c:0": {"amount_sat": 50000, "payout_script": "52", "parents": []},
        },
        "payouts": [{"txid": "payment", "coins": ["a:0", "b:0", "c:0"]}],
        "transactions": {
            "fund": {"txid": "fund", "vout": [{"value": "0.00121000"}]},
            "payment": {"txid": "payment", "vin": [{"txid": "fund", "vout": 0}], "vout": [
                {"value": "0.00069850", "scriptPubKey": {"hex": "51"}},
                {"value": "0.00049850", "scriptPubKey": {"hex": "52"}},
                {"value": "0.00001000", "scriptPubKey": {"hex": "53"}},
            ]},
        },
    }


class OracleSensitivity(unittest.TestCase):
    def test_split_and_shared_key_are_not_duplicate_settlement(self):
        result = verify(evidence())
        self.assertEqual(result["settled_coins"], 3)
        self.assertEqual(result["mining_fees_sat"], {"payment": 300})

    def test_operator_subsidy(self):
        data = evidence()
        data["coins"]["c:0"]["amount_sat"] = 49850
        with self.assertRaisesRegex(AssertionError, "deductions 150 != mining fee 300"):
            verify(data)

    def test_recipient_overcharge(self):
        data = evidence()
        data["coins"]["c:0"]["amount_sat"] += 1
        with self.assertRaisesRegex(AssertionError, "deductions 301 != mining fee 300"):
            verify(data)

    def test_wrong_key(self):
        data = evidence()
        data["coins"]["c:0"]["payout_script"] = "54"
        with self.assertRaisesRegex(AssertionError, "recipient output"):
            verify(data)

    def test_distinct_payments_for_one_coin(self):
        data = evidence()
        second = deepcopy(data["payouts"][0])
        second["txid"] = "second"
        data["payouts"].append(second)
        with self.assertRaisesRegex(AssertionError, "multiple settlements"):
            verify(data)

    def test_exit_of_predecessor_and_payout_of_successor(self):
        data = evidence()
        data["exits"] = [{"txid": "claim", "coin": "root:0"}]
        data["transactions"]["claim"] = {"txid": "claim", "vin": [{"txid": "root", "vout": 0}]}
        with self.assertRaisesRegex(AssertionError, "settled predecessor and replacement"):
            verify(data)


if __name__ == "__main__":
    unittest.main()
