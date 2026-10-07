#!/bin/bash
# A exits before expiry and its leaf confirms. B shares the round, but
# only B's branch is swept. B is paid; A keeps its on-chain exit output.
. "$(dirname "$0")/lib.sh"
mkcfg
A=$(wname a); B=$(wname b); newwallet "$A"; newwallet "$B"
round 60000 "$A" "$B"
XA=$(coins "$A"); XB=$(coins "$B")
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
bark "$A" exit start --vtxo "$XA" > "$LOG/exit-start.log" 2>&1 || say "exit start failed"
for i in $(seq 30); do
	[ -n "$(leaf_confirmed "$XA")" ] && break
	bark "$A" exit progress >> "$LOG/exit-progress.log" 2>&1; mine 1; sleep 2
done
check "A's leaf confirmed (exit done)" test -n "$(leaf_confirmed "$XA")"
expire_and_sweep "$XA $XB" || finish
# Simulate a lagging leaf-confirmation record: A now reaches the candidate
# path check. Its live output must still win over B's swept sibling branch.
AH=$(leaf_confirmed "$XA")
[ -n "$AH" ] || finish
q "UPDATE vtxo SET confirmed_height=NULL, updated_at=NOW() WHERE vtxo_id='$XA'" > /dev/null || exit 2
restore_height() { q "UPDATE vtxo SET confirmed_height=$AH, updated_at=NOW() WHERE vtxo_id='$XA'" > /dev/null; }
trap restore_height EXIT
eq "$(leaf_confirmed "$XA")" "" || exit 2
check "A remains an otherwise eligible ledger coin" eq "$(spend_state "$XA")" spendable
check "swept branch B paid" pay_until "$XB" 3
check "A exercised the path check without the leaf-height filter" test -z "$(leaf_confirmed "$XA")"
assert_paid "$XB" "$PKB" "$AMTB"
check "A never paid" eq "$(payout_state "$XA")" ""
check "A never banned" eq "$(bans "$XA")" 0
check "B is not quarantined" eq "$(quarantine_reason "$XB")" ""
ATX=${XA%:*}; AVOUT=${XA##*:}
btc gettxout "$ATX" "$AVOUT" > "$LOG/exited-output.json"
check "A still owns the unspent exit output" python3 -c "import json;assert json.load(open('$LOG/exited-output.json')) is not None"
restore_height
trap - EXIT
confirm_payouts
finish
