#!/bin/bash
# #22/#77/O17: bitcoind down -> tick fails, nothing claimed; payout wallet
# unloaded -> tick fails loudly, nothing claimed; bitcoind restarted -> the
# wallet comes back (load_on_startup) and the coin is paid.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"
round 60000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
expire_and_sweep "$X" || finish
nothing() {
	check "$1: no ban" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id='$X'")" 0
	check "$1: no claim" eq "$(payout_state "$X")" ""
}

btc unloadwallet payout > /dev/null
tick; RC=$?
check "wallet unloaded: tick fails, loudly" grep -q "tick failed" "$LOG/tick.log"
check "wallet unloaded: process exits 0 (--once), no crash ($RC)" eq "$RC" 0
nothing "wallet unloaded"
btc loadwallet payout > /dev/null

(cd "$R" && docker compose stop bitcoind > /dev/null 2>&1)
TICK_TIMEOUT=60 tick; RC=$?
check "bitcoind down: tick fails" grep -q "tick failed" "$LOG/tick.log"
nothing "bitcoind down"
(cd "$R" && docker compose start bitcoind > /dev/null 2>&1)
healthy bitcoind
for i in $(seq 30); do btc -rpcwallet=payout getwalletinfo > /dev/null 2>&1 && break; sleep 1; done
check "payout wallet reloaded on startup" test -n "$(btc -rpcwallet=payout getwalletinfo 2>/dev/null)"
healthy captaind 180
check "captaind healthy again" healthy captaind 5
ensure_fees
check "paid after restart" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
