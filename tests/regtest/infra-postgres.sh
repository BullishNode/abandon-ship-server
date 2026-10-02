#!/bin/bash
# One leader only; on Postgres connection loss the
# sidecar exits (it must not run on without its advisory lock). After the
# restart a fresh process pays normally.
. "$(dirname "$0")/lib.sh"
mkcfg poll_interval_secs=2
W=$(wname a); newwallet "$W"
round 60000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"

start_loop
check "loop running" eq "$(loop_exit 1)" running
tick; RC=$?
check "second instance refused (exit $RC)" test $RC -ne 0
check "reason: leader lock" grep -q "another sidecar instance holds the leader lock" "$LOG/tick.log"

(cd "$R" && docker compose restart postgres > /dev/null 2>&1)
RC=$(loop_exit 60)
check "loop exits on connection loss (exit $RC)" test "$RC" != running -a "$RC" != 0
[ "$RC" = running ] && stop_loop
healthy postgres; healthy captaind 180
check "captaind healthy again" healthy captaind 5

expire_and_sweep "$X" || finish
check "fresh process pays" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
