#!/bin/bash
# C8/C10: the sidecar's ledger loses a payout (bug or partial restore):
# - a paid row reset to 'claimed' -> journal: the sidecar stops;
# - a paid row deleted            -> the coin is spent: no repay.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 80000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
TRA=$(tr_address "$PK")
expire_and_sweep "$X" || finish
check "paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
SAVED=$(q "SELECT anchor_point, amount_sat, address, state, txid, encode(raw_tx, 'hex'), claimed_at FROM sidecar.payout WHERE vtxo_id='$X'")
IFS='|' read -r S_ANCHOR S_AMT S_ADDR S_STATE S_TXID S_RAW S_AT <<< "$SAVED"

q "UPDATE sidecar.payout SET state='claimed', txid=NULL, raw_tx=NULL WHERE vtxo_id='$X'" > /dev/null
tick; RC=$?
check "row reset: sidecar stops (exit $RC)" test $RC -ne 0
check "row reset: reason" grep -q "already paid (journal)" "$LOG/tick.log"

q "DELETE FROM sidecar.payout WHERE vtxo_id='$X'" > /dev/null
tick; RC=$?
check "row deleted: tick runs (exit $RC)" test $RC -eq 0
check "row deleted: coin still spent" eq "$(spend_state "$X")" spent
check "row deleted: no new row" eq "$(payout_state "$X")" ""

q "INSERT INTO sidecar.payout (vtxo_id, anchor_point, amount_sat, address, state, txid, raw_tx, claimed_at)
   VALUES ('$X', '$S_ANCHOR', $S_AMT, '$S_ADDR', '$S_STATE', '$S_TXID', decode('$S_RAW', 'hex'), '$S_AT')" > /dev/null
tick
check "one payout tx ever sent to tr(key)" eq "$(wallet_sends_to "$TRA")" 1
check "journaled once" eq "$(journaled "$X")" 1
finish
