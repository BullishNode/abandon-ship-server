#!/bin/bash
# #13: a 3000-sat coin whose fee share (230 vB x estimate) is above
# max_fee_pct_per_payout (20%) is left alone: no ban, no claim, no
# quarantine, still refreshable. With the rule relaxed to 60% it is paid.
. "$(dirname "$0")/lib.sh"
mkcfg
S=$(wname sender); T=$(wname small); newwallet "$S"; newwallet "$T" 0
round 100000 "$S"
bark "$S" send "$(arkaddr "$T")" "3000 sat" > "$LOG/send.log" || say "send failed"
X=$(coins "$T"); read -r PK AMT <<< "$(coininfo "$T" "$X")"
check "a 3000-sat coin" eq "$AMT" 3000
expire_and_sweep "$X" || finish
for i in 1 2; do tick; sleep $(( $(ban_wait) + 1 )); done
check "20%: not banned" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id='$X'")" 0
check "20%: not claimed" eq "$(payout_state "$X")" ""
check "20%: not quarantined" eq "$(quarantine_reason "$X")" ""
check "20%: still spendable" eq "$(spend_state "$X")" spendable

mkcfg max_fee_pct_per_payout=60
check "60%: paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT" 60
confirm_payouts
finish
