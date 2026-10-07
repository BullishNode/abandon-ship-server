#!/bin/bash
# bitcoind's estimator has no answer (no -fallbackfee): no ban, no
# claim, no payout. Once it has data again, the coin is paid at that rate.
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
# Empty blocks age the estimator's data out.
for i in $(seq 40); do btc estimatesmartfee 6 | grep -q '"feerate"' || break; mine 25; done
check "estimator empty" test -z "$(btc estimatesmartfee 6 | grep '"feerate"')"
# Exercise the process default directly: tick() intentionally sets RUST_LOG.
for mode in unset invalid error; do
	case "$mode" in
		unset) env -u RUST_LOG "$BIN" "$CFG" --once > "$LOG/log-$mode.txt" 2>&1 ;;
		invalid) RUST_LOG='abandon_ship_server=not_a_level' "$BIN" "$CFG" --once > "$LOG/log-$mode.txt" 2>&1 ;;
		error) RUST_LOG=error "$BIN" "$CFG" --once > "$LOG/log-$mode.txt" 2>&1 ;;
	esac
	check "$mode logging tick completed" eq "$?" 0
	if [ "$mode" = error ]; then
		check "explicit error level hides warnings" test "$(grep -c 'no fee estimate' "$LOG/log-$mode.txt")" = 0
		check "explicit error level hides summary" test "$(grep -c 'tick summary' "$LOG/log-$mode.txt")" = 0
	else
		check "$mode filter exposes missing fee estimate" grep -q 'no fee estimate:' "$LOG/log-$mode.txt"
		check "$mode filter has exactly one tick summary" test "$(grep -c 'tick summary' "$LOG/log-$mode.txt")" = 1
		check "$mode summary reports no selection or claims" grep -qE 'candidates=0.*claims=0.*success=true' <(sed 's/\x1b\[[0-9;]*m//g' "$LOG/log-$mode.txt")
	fi
done
check "no ban" eq "$(bans "$X")" 0
check "no claim" eq "$(payout_state "$X")" ""
check "still spendable" eq "$(spend_state "$X")" spendable
ensure_fees
check "estimate back: paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
