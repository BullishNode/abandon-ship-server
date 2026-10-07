#!/bin/bash
# The payout wallet holds only many small UTXOs: funding the payout takes so
# many inputs that the coin's real fee share is above max_fee_pct_per_payout,
# although the claim-time bound allowed it. The built tx is refused before it
# is stored (nothing signed, nothing broadcast); once the wallet has a large
# UTXO again, the coin is paid.
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
RATE=$(btc estimatesmartfee 6 | python3 -c "import json,sys;print(json.load(sys.stdin)['feerate']*1e5)")
# Twice the claim-time bound (230 vB), far below 32 inputs' real share (> 1800 vB).
PCT=$(python3 -c "import math;print(math.ceil(2*230*$RATE*100/$AMT))")
mkcfg max_fee_pct_per_payout=$PCT
say "rate $RATE sat/vB, max_fee_pct_per_payout=$PCT"
trap 'unlock_wallet' EXIT
lock_wallet
OUTS=$(for i in $(seq 32); do printf '"%s":0.00002,' "$(btc -rpcwallet=payout getnewaddress)"; done)
btc -rpcwallet=faucet -named sendmany amounts="{${OUTS%,}}" fee_rate=5 > /dev/null
mine 1

check "claimed" pay_until "$X" 3 "'claimed'"
tick
check "built tx refused: payout deferred" grep -q "payout deferred" "$LOG/tick.log"
check "still claimed, no tx stored" eq "$(payout_state "$X")|$(payout_txid "$X")" "claimed|"
unlock_wallet
check "large UTXO back: paid" pay_until "$X" 2
assert_paid "$X" "$PK" "$AMT" "$PCT"

# Consolidate the small UTXOs so later scenarios fund from one input.
SMALL=$(btc -rpcwallet=payout listunspent 1 | python3 -c "import json,sys
print(json.dumps([{'txid':u['txid'],'vout':u['vout']} for u in json.load(sys.stdin) if round(u['amount']*1e8)==2000]))")
btc -rpcwallet=payout -named sendall recipients="[\"$(btc -rpcwallet=payout getnewaddress)\"]" inputs="$SMALL" fee_rate=2 > /dev/null
confirm_payouts
finish
