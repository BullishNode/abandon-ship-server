#!/bin/bash
# One expired round coin, owner offline: paid once to tr(coin key);
# the owner's later refresh is refused. Also checks the payout feerate equals
# bitcoind's estimate (untyped walletcreatefundedpsbt/finalizepsbt path).
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 100000 "$W"
ID=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$ID")"
say "coin $ID ($AMT sat)"
expire_and_sweep "$ID" || finish

check "paid within 3 ticks" pay_until "$ID" 3
assert_paid "$ID" "$PK" "$AMT"

TXID=$(payout_txid "$ID")
EST=$(btc estimatesmartfee 6 | python3 -c "import json,sys;print(json.load(sys.stdin)['feerate']*1e5)")
RATE=$(btc getmempoolentry "$TXID" | python3 -c "import json,sys;e=json.load(sys.stdin);print(e['fees']['base']*1e8/e['vsize'])")
check "payout feerate $RATE = estimate $EST (sat/vB)" python3 -c "import sys; sys.exit(abs($RATE-$EST) > 0.02*$EST)"

confirm_payouts
check "payout confirmed" eq "$(payout_state "$ID")" confirmed

"$R/bark" "$W" refresh --vtxo "$ID" > "$LOG/refresh-after.log" 2>&1
check "owner's refresh of the paid coin refused" grep -qiE 'error|unusable|not spendable' "$LOG/refresh-after.log"
check "coin still spent" eq "$(spend_state "$ID")" spent
check "one payout tx ever sent to tr(key)" eq "$(wallet_sends_to "$(tr_address "$PK")")" 1
finish
