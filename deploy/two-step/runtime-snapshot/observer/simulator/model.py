from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from typing import Any

SIGNET_GENESIS = "00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6"
SOURCES = {"registered", "pending_board", "unregistered", "lightning_receive",
           "lightning_send", "hark_unclaimed"}


class InvariantError(ValueError):
    """Evidence contradicts a monetary or ownership invariant."""


def integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def timestamp(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative timestamp")
    return value


def btc_to_sat(value: Any) -> int:
    if isinstance(value, (float, bool)):
        raise ValueError("BTC amounts must be decimal strings or exact Decimal values")
    amount = Decimal(value) * 100_000_000
    if not amount.is_finite() or amount != amount.to_integral_value() or amount < 0:
        raise ValueError("invalid BTC amount")
    return int(amount)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@lru_cache(maxsize=1)
def _bip39_words():
    return frozenset(Path(__file__).with_name('bip39-english.txt').read_text().splitlines())


def public_payload(value: Any) -> None:
    """Reject secret-shaped fields before writing the public event trail."""
    if isinstance(value, dict):
        for key, item in value.items():
            lower = key.lower()
            if lower in {'preimage','payment_preimage','secret_key','seckey','xprv','tprv'} or any(word in lower for word in ("mnemonic", "private_key", "password", "auth_token", "cookie")):
                raise ValueError(f"secret field is forbidden in public evidence: {key}")
            public_payload(item)
    elif isinstance(value, list):
        for item in value:
            public_payload(item)
    elif isinstance(value, str):
        if re.search(r'(?i)\b(mnemonic|private[_ -]?key|password|auth[_ -]?token|payment[_ -]?preimage|secret[_ -]?key|seckey)\b|\bBearer\s+\S+|\bBasic\s+[A-Za-z0-9+/]+=*|__cookie__:|\b[xt]prv[1-9A-HJ-NP-Za-km-z]+\b|\bseed\s*[:=]\s*[0-9a-f]{64}\b', value):
            raise ValueError('secret-shaped text is forbidden in public evidence')
        if re.search(r'(?i)\bpreimage\s*[:=]\s*(?:Some\()?\s*[\x22\x27]?[0-9a-f]{64}\b',value):
            raise ValueError('preimage-shaped text is forbidden in public evidence')
        # SDK timestamps/prefixes can surround an unlabeled phrase. Check each
        # contiguous BIP39-word run, including punctuation-delimited log lines.
        run=0;dictionary=_bip39_words()
        for word in re.findall(r'[a-z]+|[^a-z\s]+',value.lower()):
            run=run+1 if word in dictionary else 0
            if run>=12:raise ValueError('mnemonic-shaped text is forbidden in public evidence')


def fee_allocation(gross: dict[str, int], recipient_fee_sat: int) -> dict[str, int]:
    """Integer proportional fees; deterministic tie break by destination script."""
    fee = integer(recipient_fee_sat, "recipient fee")
    ordered = sorted(gross)
    total = sum(integer(gross[k], "gross", 1) for k in ordered)
    if not ordered or fee >= total:
        raise InvariantError("recipient fee consumes the gross entitlement")
    shares = {k: gross[k] * fee // total for k in ordered}
    remaining = fee - sum(shares.values())
    ranked = sorted(ordered, key=lambda k: (-(gross[k] * fee % total), k))
    for key in ranked[:remaining]:
        shares[key] += 1
    return shares


def unique_settlement_fee(rows: list[dict]) -> int:
    """fee_sat is repeated per input, not an independently chargeable coin fee."""
    fees: dict[str, int] = {}
    for row in rows:
        txid, fee = row["txid"], integer(row["fee_sat"], "fee_sat")
        if txid in fees and fees[txid] != fee:
            raise InvariantError("inconsistent fee for the same settlement transaction")
        fees[txid] = fee
    return sum(fees.values())


def transaction_fee(tx: dict) -> int:
    inputs = sum(integer(i["amount_sat"], "input amount") for i in tx["inputs"])
    outputs = sum(integer(o["amount_sat"], "output amount") for o in tx["outputs"])
    if inputs < outputs:
        raise InvariantError("transaction creates value")
    return inputs - outputs


def capital_summary(snapshots) -> dict:
    """Stream epoch totals; database iterators must be in observation-time order."""
    ordered = sorted(snapshots, key=lambda x: x["observed_at"]) if isinstance(snapshots, list) else snapshots
    samples = gaps = incomplete = valid = 0
    opening = closing = minimum = high_water = locked_peak = previous = None
    drawdown = capital_seconds = intervals = 0
    for sample in ordered:
        samples += 1
        timestamp(sample["observed_at"], "snapshot time")
        incomplete += sample.get("complete") is not True
        value = sample.get("rounds_confirmed_sat")
        gaps += value is None or bool(sample.get("error"))
        if value is not None:
            value = integer(value, "rounds confirmed")
            valid += 1
            if opening is None:
                opening = minimum = high_water = value
            closing = value
            minimum = min(minimum, value)
            high_water = max(high_water, value)
            drawdown = max(drawdown, high_water - value)
        locked = sample.get("tree_locked_sat")
        if locked is not None:
            locked = integer(locked, "tree locked")
            locked_peak = max(locked_peak or 0, locked)
        if (previous is not None and previous.get("tree_locked_sat") is not None
                and locked is not None and previous.get("complete") is True and sample.get("complete") is True):
            capital_seconds += (sample["observed_at"] - previous["observed_at"]) * previous["tree_locked_sat"]
            intervals += 1
        previous = sample
    if not valid:
        return {"status":"unknown", "samples":samples, "observation_gaps":gaps,
                "minimum_required_capital_sat":None}
    return {
        "status": "partial" if incomplete else "observed", "samples": samples,
        "opening_rounds_confirmed_sat": opening, "closing_rounds_confirmed_sat": closing,
        "minimum_observed_rounds_confirmed_sat": minimum,
        "peak_observed_rounds_drawdown_sat": drawdown,
        "observed_rounds_change_from_opening_sat": closing - opening,
        "peak_observed_tree_locked_sat": locked_peak,
        "tree_capital_days_sat": capital_seconds / 86400 if intervals else None,
        "observation_gaps": gaps, "incomplete_capital_samples": incomplete,
        "minimum_required_capital_sat": None,
        "interpretation": "Observed balances/drawdown, not an experimentally established minimum; injections and spendability change the interpretation.",
    }
