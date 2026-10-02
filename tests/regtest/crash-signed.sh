#!/bin/bash
# Claim committed, payout not built (funding fails): the next run pays
# once. Tx stored ('signed') but the process dies before the
# journal write and the broadcast (journal unwritable): the next run journals
# it and broadcasts the same stored tx; nothing else is built.
. "$(dirname "$0")/lib.sh"
mkcfg
WA=$(wname a); WB=$(wname b); newwallet "$WA"; newwallet "$WB"
round 60000 "$WA" "$WB"
XA=$(coins "$WA"); XB=$(coins "$WB")
read -r PKA AMTA <<< "$(coininfo "$WA" "$XA")"; read -r PKB AMTB <<< "$(coininfo "$WB" "$XB")"
expire_and_sweep "$XA $XB" || finish
trap 'unlock_wallet; chmod u+w "$JOURNAL"' EXIT

lock_wallet
check "claimed, payout not built" pay_until "$XA $XB" 3 "'claimed'"
unlock_wallet

chmod a-w "$JOURNAL"
tick
chmod u+w "$JOURNAL"
check "journal write failed" grep -q "Permission denied" "$LOG/tick.log"
check "stored as signed" eq "$(payout_state "$XA") $(payout_state "$XB")" "signed signed"
TXID=$(payout_txid "$XA")
check "one stored tx for both" eq "$(payout_txid "$XB")" "$TXID"
check "not broadcast" test -z "$(btc getmempoolentry "$TXID" 2>/dev/null)"
check "not journaled yet" eq "$(journaled "$XA")" 0

tick
check "restart: journaled" eq "$(journaled "$XA") $(journaled "$XB")" "1 1"
check "restart: the stored tx is in the mempool" test -n "$(btc getmempoolentry "$TXID" 2>/dev/null)"
check "restart: state broadcast" eq "$(payout_state "$XA")" broadcast
assert_paid "$XA" "$PKA" "$AMTA"
assert_paid "$XB" "$PKB" "$AMTB"
confirm_payouts
check "only that tx paid them" eq "$(wallet_sends_to "$(tr_address "$PKA")") $(wallet_sends_to "$(tr_address "$PKB")")" "1 1"
finish
