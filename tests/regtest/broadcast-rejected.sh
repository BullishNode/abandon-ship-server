#!/bin/bash
# Core rejects one stored payout for its modified fee. Other coins still pay,
# and the rejected transaction's inputs stay reserved until it is accepted.
. "$(dirname "$0")/lib.sh"
A=$(wname a); B=$(wname b)
newwallet "$A"; newwallet "$B"
round 5000000 "$A"; round 5000000 "$B"
XA=$(coins "$A"); XB=$(coins "$B")
read -r PKA AMTA <<< "$(coininfo "$A" "$XA")"
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XA $XB" || finish

REJECTED=
cleanup() {
	chmod u+w "$JOURNAL"
	[ -z "$REJECTED" ] || btc prioritisetransaction "$REJECTED" 0 100000 > /dev/null
	unlock_wallet
}
trap cleanup EXIT
lock_wallet
btc -rpcwallet=faucet -named sendtoaddress address="$(btc -rpcwallet=payout getnewaddress)" amount=0.06 fee_rate=5 > /dev/null
mine 1
# Only A is past grace. Stop after storing its signed transaction.
EB=$(q "SELECT expiry FROM vtxo WHERE vtxo_id='$XB'")
mkcfg min_payout_sat=4000000 grace_blocks=$(( $(tip) - EB + 1 ))
chmod a-w "$JOURNAL"
check "A stored before journal append" pay_until "$XA" 3 "'signed'"
chmod u+w "$JOURNAL"
TX=$(payout_txid "$XA")
RAW=$(q "SELECT encode(raw_tx,'hex') FROM sidecar.payout WHERE vtxo_id='$XA'")
check "B not claimed yet" eq "$(payout_state "$XB")" ""
[ -n "$TX" ] || finish
btc prioritisetransaction "$TX" 0 -100000 > /dev/null
REJECTED=$TX
btc testmempoolaccept "[\"$RAW\"]" > "$LOG/rejection.json"
check "Core rejects A for its fee" grep -q 'min relay fee not met' "$LOG/rejection.json"

# A second UTXO can fund B; A's UTXO must not be reused.
btc -rpcwallet=faucet -named sendtoaddress address="$(btc -rpcwallet=payout getnewaddress)" amount=0.06 fee_rate=5 > /dev/null
mine 1
mkcfg min_payout_sat=4000000
check "B pays while A is rejected" pay_until "$XB" 3
check "A remains signed" eq "$(payout_state "$XA")" signed
btc decoderawtransaction "$RAW" > "$LOG/signed.json"
btc -rpcwallet=payout listlockunspent > "$LOG/locks.json"
check "every input of A remains reserved" python3 - "$LOG" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
tx=json.loads((p/'signed.json').read_text())
locked={(u['txid'],u['vout']) for u in json.loads((p/'locks.json').read_text())}
assert all((u['txid'],u['vout']) in locked for u in tx['vin'])
PY

btc prioritisetransaction "$TX" 0 100000 > /dev/null
REJECTED=
check "fee rejection lifted: both paid" pay_until "$XA $XB" 3
check "A uses the original stored transaction" eq "$(payout_txid "$XA")" "$TX"
assert_paid "$XA" "$PKA" "$AMTA"
assert_paid "$XB" "$PKB" "$AMTB"
confirm_payouts
finish
