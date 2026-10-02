#!/bin/bash
# captaind's DB is restored from a backup taken before the
# claim: the paid coin is spendable again, unbanned, with no ledger row.
# Runbook order (sidecar first): the first tick re-marks it spent from the
# journal and quarantines it; nothing is paid twice; the owner's refresh is
# refused.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 70000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
TRA=$(tr_address "$PK")
expire_and_sweep "$X" || finish
check "paid" pay_until "$X" 3
confirm_payouts
check "payout confirmed" eq "$(payout_state "$X")" confirmed
RAW=$(grep "^$X " "$JOURNAL" | cut -d' ' -f3)
check "journal carries the raw payout tx" eq "$(btc decoderawtransaction "${RAW:-00}" | sed -n 's/^  "txid": "\(.*\)",/\1/p')" "$(payout_txid "$X")"

# The "restore": coin row as before the ban and claim, ledger rows gone.
q "UPDATE vtxo SET spend_state='spendable', banned_until_height=NULL, updated_at=NOW() WHERE vtxo_id='$X'" > /dev/null
q "DELETE FROM sidecar.payout WHERE vtxo_id='$X'; DELETE FROM sidecar.ban WHERE vtxo_id='$X'" > /dev/null
check "restored coin is spendable again" eq "$(spend_state "$X")" spendable

tick
check "first tick re-marks it spent" eq "$(spend_state "$X")" spent
check "and quarantines it" eq "$(quarantine_reason "$X")" "already paid per local journal"
check "journaled tx re-sent without error (already mined)" test -z "$(grep "not accepted" "$LOG/tick.log")"
for i in 1 2; do sleep $(( $(ban_wait) + 1 )); tick; done
check "no new ledger row" eq "$(payout_state "$X")" ""
check "one payout tx ever sent to tr(key)" eq "$(wallet_sends_to "$TRA")" 1
"$R/bark" "$W" refresh --vtxo "$X" > "$LOG/refresh.log" 2>&1
check "owner's refresh refused" grep -qiE 'error' "$LOG/refresh.log"
check "coin still spent" eq "$(spend_state "$X")" spent
finish
