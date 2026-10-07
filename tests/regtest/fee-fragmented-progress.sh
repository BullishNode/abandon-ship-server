#!/bin/bash
# A large claim needs many small wallet inputs and fails its actual fee cap.
# A later, smaller entitlement fits one input and must still be paid.
. "$(dirname "$0")/lib.sh"
BATCH=${BATCH:-1}
mkcfg max_batch=$BATCH
A=$(wname a); B=$(wname b)
newwallet "$A"; round 60000 "$A"
XA=$(coins "$A"); read -r PKA AMTA <<< "$(coininfo "$A" "$XA")"
newwallet "$B"; round 30000 "$B"
XB=$(coins "$B"); read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XA $XB" || finish
RATE=$(btc estimatesmartfee 6 | python3 -c "import json,sys;print(json.load(sys.stdin)['feerate']*1e5)")
# A's estimate is below this cap, but ten or more inputs exceed it.
PCT=$(python3 -c "import math;print(math.ceil(2.2*230*$RATE*100/$AMTA))")
cleanup() { unlock_wallet; }
trap cleanup EXIT
lock_wallet
OUTS=$(for i in $(seq 32); do printf '"%s":0.00002,' "$(btc -rpcwallet=payout getnewaddress)"; done)
btc -rpcwallet=faucet -named sendmany amounts="{${OUTS%,}}" fee_rate=5 > /dev/null
mine 1
EB=$(q "SELECT expiry FROM vtxo WHERE vtxo_id='$XB'")
mkcfg max_batch=$BATCH max_fee_pct_per_payout=$PCT grace_blocks=$(( $(tip) - EB + 1 ))
check "A claimed but not signed" pay_until "$XA" 3 "'claimed'"
check "actual fee defers A" grep -Eq 'payout (funding )?deferred' "$LOG/tick.log"

# This input is enough for B alone, but A still needs many small inputs.
btc -rpcwallet=faucet -named sendtoaddress address="$(btc -rpcwallet=payout getnewaddress)" amount=0.00031 fee_rate=5 > /dev/null
mine 1
mkcfg max_batch=$BATCH max_fee_pct_per_payout=$PCT
check "B pays while A is still unaffordable" pay_until "$XB" 3
check "A remains recoverable as a claim" eq "$(payout_state "$XA")|$(payout_txid "$XA")" 'claimed|'
assert_paid "$XB" "$PKB" "$AMTB" "$PCT"

unlock_wallet
check "A pays when suitable funding returns" pay_until "$XA" 3
assert_paid "$XA" "$PKA" "$AMTA" "$PCT"
# Consolidate this fixture's small inputs before the next scenario. Core's
# fee-subtraction selection can keep choosing them even after funding returns.
SMALL=$(btc -rpcwallet=payout listunspent 1 | python3 -c "import json,sys
print(json.dumps([{'txid':u['txid'],'vout':u['vout']} for u in json.load(sys.stdin) if round(u['amount']*1e8)==2000]))")
if [ "$SMALL" != '[]' ]; then
	btc -rpcwallet=payout -named sendall recipients="[\"$(btc -rpcwallet=payout getnewaddress)\"]" inputs="$SMALL" fee_rate=2 > /dev/null
fi
confirm_payouts
finish
