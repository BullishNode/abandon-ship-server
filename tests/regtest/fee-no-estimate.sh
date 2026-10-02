#!/bin/bash
# O23: bitcoind's estimator has no answer (no -fallbackfee): no ban, no
# claim, no payout. Once it has data again, the coin is paid at that rate.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 60000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
expire_and_sweep "$X" || finish
# Empty blocks age the estimator's data out.
for i in $(seq 40); do btc estimatesmartfee 6 | grep -q '"feerate"' || break; mine 25; done
check "estimator empty" test -z "$(btc estimatesmartfee 6 | grep '"feerate"')"
for i in 1 2; do tick; sleep $(( $(ban_wait) + 1 )); done
check "logged 'no fee estimate'" grep -q "no fee estimate: not claiming or paying" "$LOG/tick.log"
check "no ban" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id='$X'")" 0
check "no claim" eq "$(payout_state "$X")" ""
check "still spendable" eq "$(spend_state "$X")" spendable
ensure_fees
check "estimate back: paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
