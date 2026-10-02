#!/bin/bash
# C6/C7/C8/C10: a DB writer edits the sidecar's own ledger.
# - amount or address of a claimed row edited -> re-derived from the
#   validated VTXO at pay time: the sidecar stops, nothing is paid;
# - a paid row reset to 'claimed'            -> journal: the sidecar stops;
# - a paid row deleted                       -> the coin is spent: no repay.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 80000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
TRA=$(tr_address "$PK")
expire_and_sweep "$X" || finish
trap unlock_wallet EXIT

lock_wallet
check "claimed, unpaid (wallet locked)" pay_until "$X" 3 "'claimed'"
unlock_wallet
row() { q "SELECT amount_sat, address, state FROM sidecar.payout WHERE vtxo_id='$X'"; }
ORIG=$(row)

q "UPDATE sidecar.payout SET amount_sat = amount_sat + 5000 WHERE vtxo_id='$X'" > /dev/null
tick; RC=$?
check "amount edit: sidecar stops (exit $RC)" test $RC -ne 0
check "amount edit: reason" grep -q "differs from its validated VTXO" "$LOG/tick.log"
check "amount edit: nothing paid" eq "$(payout_state "$X")" claimed
q "UPDATE sidecar.payout SET amount_sat = amount_sat - 5000 WHERE vtxo_id='$X'" > /dev/null

EVIL=$(btc -rpcwallet=faucet getnewaddress "" bech32m)
q "UPDATE sidecar.payout SET address = '$EVIL' WHERE vtxo_id='$X'" > /dev/null
tick; RC=$?
check "address edit: sidecar stops (exit $RC)" test $RC -ne 0
check "address edit: reason" grep -q "differs from its validated VTXO" "$LOG/tick.log"
check "address edit: nothing sent to the attacker" eq "$(wallet_sends_to "$EVIL")" 0
q "UPDATE sidecar.payout SET address = '$TRA' WHERE vtxo_id='$X'" > /dev/null
check "row restored" eq "$(row)" "$ORIG"

check "paid after restore" pay_until "$X" 2
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
