#!/bin/bash
# bitcoind's estimator has no answer (no -fallbackfee): no ban, no
# claim, no payout. Once it has data again, the coin is paid at that rate.
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
# Empty blocks age the estimator's data out.
for i in $(seq 40); do btc estimatesmartfee 6 | grep -q '"feerate"' || break; mine 25; done
check "estimator empty" test -z "$(btc estimatesmartfee 6 | grep '"feerate"')"
ticks 2
check "logged 'no fee estimate'" grep -q "no fee estimate: not claiming or building payouts" "$LOG/tick.log"
check "no ban" eq "$(bans "$X")" 0
check "no claim" eq "$(payout_state "$X")" ""
check "still spendable" eq "$(spend_state "$X")" spendable
ensure_fees
check "estimate back: paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
