#!/bin/bash
# Two arkoor payments to the same Ark address give two coins with one
# key. The payout has one output to tr(key) carrying the sum.
. "$(dirname "$0")/lib.sh"
mkcfg
S=$(wname sender); T=$(wname receiver)
newwallet "$S"; newwallet "$T" 0
round 200000 "$S"
ADDR=$(arkaddr "$T")
bark "$S" send "$ADDR" "40000 sat" > "$LOG/send1.log" || say "send 1 failed"
bark "$S" send "$ADDR" "30000 sat" > "$LOG/send2.log" || say "send 2 failed"
mapfile -t IDS < <(coins "$T")
check "receiver holds two coins" eq "${#IDS[@]}" 2
read -r PK1 A1 <<< "$(coininfo "$T" "${IDS[0]}")"; read -r PK2 A2 <<< "$(coininfo "$T" "${IDS[1]}")"
check "both coins share one key" eq "$PK1" "$PK2"
expire_and_sweep "${IDS[*]}" || finish

check "both paid within 3 ticks" pay_until "${IDS[*]}" 3
TXID=$(payout_txid "${IDS[0]}")
assert_payout_fee "$TXID"
check "same payout tx" eq "$(payout_txid "${IDS[1]}")" "$TXID"
TRA=$(tr_address "$PK1")
N=$(btc getrawtransaction "$TXID" 1 | python3 -c "import json,sys
print(sum(1 for o in json.load(sys.stdin)['vout'] if o['scriptPubKey'].get('address')=='$TRA'))")
check "exactly one output to tr(key)" eq "$N" 1
SUM=$((A1 + A2)); GOT=$(paid_to "$TXID" "$TRA")
check "output carries the sum $SUM minus fee share (got $GOT)" test "$GOT" -le "$SUM" -a "$GOT" -ge $((SUM * 80 / 100)) -a "$GOT" -gt "$A1" -a "$GOT" -gt "$A2"
confirm_payouts
finish
