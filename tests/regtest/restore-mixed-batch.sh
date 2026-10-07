#!/bin/bash
# A partial ledger restore leaves one confirmed batch member and one claimed
# member. The payment itself is real and confirmed; only the restored row is
# a fixture. Its peer must not suppress reconciliation of the original bytes.
. "$(dirname "$0")/lib.sh"
mkcfg
A=$(wname a); B=$(wname b); newwallet "$A"; newwallet "$B"
round 60000 "$A" "$B"
XA=$(coins "$A"); XB=$(coins "$B")
read -r PKA AMTA <<< "$(coininfo "$A" "$XA")"
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XA $XB" || finish
check "both fixture coins paid" pay_until "$XA $XB" 3
TX=$(payout_txid "$XA"); RAW=$(journal_raw "$XB")
[ -n "$TX" ] && [ -n "$RAW" ] || exit 2
eq "$(payout_txid "$XB")" "$TX" || exit 2
confirm_payouts
eq "$(payout_state "$XA")|$(payout_state "$XB")" 'confirmed|confirmed' || exit 2

STOPPED=0
cleanup() {
	[ "$STOPPED" = 1 ] || return 0
	# Preserve the failed assertion, then repair using the separately pinned
	# recovery binary before restarting captaind. Never delete an obligation.
	BIN=${RECOVERY_BIN:-$BIN}
	tick || return 1
	eq "$(payout_state "$XB")" confirmed || return 1
	(cd "$R" && docker compose start captaind watchmand > /dev/null) || return 1
	STOPPED=0
	healthy captaind
}
trap 'cleanup || echo "SETUP FAIL: reconcile restored batch before restarting captaind" >&2' EXIT
(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
STOPPED=1
q "UPDATE sidecar.payout SET state='claimed', txid=NULL, raw_tx=NULL WHERE vtxo_id='$XB'" > /dev/null || exit 2
eq "$(payout_state "$XA")|$(payout_state "$XB")" 'confirmed|claimed' || exit 2

check "mixed batch recovery tick succeeds" tick
check "restored member confirms despite confirmed peer" eq "$(payout_state "$XB")" confirmed
check "confirmed peer stays confirmed" eq "$(payout_state "$XA")" confirmed
check "restored member retains original txid" eq "$(payout_txid "$XB")" "$TX"
check "restored member retains original journal bytes" eq "$(journal_raw "$XB")" "$RAW"
check "A received once" eq "$(wallet_sends_to "$(tr_address "$PKA")")" 1
check "B received once" eq "$(wallet_sends_to "$(tr_address "$PKB")")" 1
cleanup || exit 2
finish
