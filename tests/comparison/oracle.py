#!/usr/bin/env python3
"""Check observed settlements against workload lineage and Bitcoin transactions.

Input: coins keyed by ID (parents, amount_sat, payout_script), payouts
(txid, coins), exits (txid, coin), and decoded Core transactions keyed by txid.
Amounts in transactions are BTC; workload amounts are integer satoshis.
This checks effective settlements, not the recoverability of pending work.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import subprocess


def sat(value):
    amount = Decimal(str(value)) * 100_000_000
    assert amount == amount.to_integral_value(), "fractional satoshi in chain evidence"
    return int(amount)


def verify(evidence):
    coins = evidence["coins"]
    transactions = evidence["transactions"]
    ancestors = {}

    def lineage(coin, visiting=frozenset()):
        assert coin in coins, f"missing workload coin {coin}"
        assert coin not in visiting, f"cyclic workload lineage at {coin}"
        if coin not in ancestors:
            parents = set(coins[coin].get("parents", []))
            ancestors[coin] = parents | set().union(*(lineage(p, visiting | {coin}) for p in parents))
        return ancestors[coin]

    for coin in coins:
        lineage(coin)

    settled = {}
    for item in evidence.get("exits", []):
        coin, txid = item["coin"], item["txid"]
        tx = transactions[txid]
        assert any(f'{i["txid"]}:{i["vout"]}' == coin for i in tx["vin"]), f"exit does not spend {coin}"
        settled.setdefault(coin, set()).add(txid)

    for item in evidence.get("payouts", []):
        assert len(set(item["coins"])) == len(item["coins"]), "duplicate coin in payout evidence"
        for coin in item["coins"]:
            settled.setdefault(coin, set()).add(item["txid"])

    for coin, payments in settled.items():
        assert len(payments) == 1, f"multiple settlements for {coin}: {sorted(payments)}"
        overlap = lineage(coin) & settled.keys()
        assert not overlap, f"settled predecessor and replacement: {sorted(overlap)} -> {coin}"

    fees = {}
    for item in evidence.get("payouts", []):
        txid = item["txid"]
        tx = transactions[txid]
        assert tx["txid"] == txid, "transaction identity mismatch"
        gross = {}
        for coin in item["coins"]:
            entry = coins[coin]
            script = entry["payout_script"]
            gross[script] = gross.get(script, 0) + entry["amount_sat"]
        deductions = 0
        for script, amount in gross.items():
            outputs = [o for o in tx["vout"] if o["scriptPubKey"]["hex"] == script]
            assert len(outputs) == 1, f"missing or duplicated recipient output in {txid}"
            net = sat(outputs[0]["value"])
            fee = amount - net
            assert 0 <= fee and net >= 330, f"invalid recipient amount in {txid}"
            assert fee * 100 <= evidence["max_fee_pct"] * amount, f"recipient fee cap exceeded in {txid}"
            deductions += fee
        input_value = sum(sat(transactions[i["txid"]]["vout"][i["vout"]]["value"]) for i in tx["vin"])
        miner_fee = input_value - sum(sat(o["value"]) for o in tx["vout"])
        assert deductions == miner_fee, f"recipient deductions {deductions} != mining fee {miner_fee} in {txid}"
        fees[txid] = miner_fee
    checks = ["settlement identity", "predecessor exclusion"]
    if fees:
        checks += ["recipient outputs", "actual fee equality and cap"]
    return {"settled_coins": len(settled), "payouts": len(fees), "mining_fees_sat": fees, "checks": checks}


def capture(evidence, bitcoin_cli):
    """Use an arm's Bitcoin wrapper; never ask its payout implementation for a verdict."""
    transactions = evidence.setdefault("transactions", {})

    def fetch(txid):
        if txid not in transactions:
            result = subprocess.run([bitcoin_cli, "getrawtransaction", txid, "1"], check=True, capture_output=True, text=True)
            transactions[txid] = json.loads(result.stdout, parse_float=Decimal)
        return transactions[txid]

    for item in evidence.get("exits", []) + evidence.get("payouts", []):
        tx = fetch(item["txid"])
        assert tx.get("confirmations", 0) >= 0, "conflicted transaction is not effective settlement"
        for input_ in tx["vin"]:
            fetch(input_["txid"])
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--bitcoin-cli", help="Capture chain facts through this executable wrapper first")
    parser.add_argument("--save", type=Path, help="Retain the captured evidence, including on failure")
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(), parse_float=Decimal)
    if args.bitcoin_cli:
        capture(evidence, args.bitcoin_cli)
    if args.save:
        args.save.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps(verify(evidence), indent=2))


if __name__ == "__main__":
    main()
