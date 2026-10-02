#!/bin/bash
# Run every scenario in order against the regtest stack (regtest/compose.yaml)
# and print a PASS/FAIL table. Usage: tests/regtest/run-all.sh [scenario...]
# Logs: $OUT (default /tmp/abandon-regtest/<scenario>/).
cd "$(dirname "$0")"
. ./lib.sh
SCENARIOS=(${@:-schema-version happy-single happy-batch shared-address unclaimed-delegated
	race-held-lock race-user-refresh race-operator-unban h2-probe
	exit-full exit-partial exit-blocked
	tamper-payout db-restore crash-signed
	fee-pct-rule fee-no-estimate circuit-breaker infra-bitcoind infra-postgres
	web-journey})

(cd "$ROOT" && cargo build -q) || { echo "build failed"; exit 2; }
for s in bitcoind postgres captaind watchmand; do healthy "$s" 60 || { echo "stack: $s not up"; exit 2; }; done

# Drain what earlier runs left: no claimed or signed rows may block the suite.
mkcfg; ensure_fees
for i in 1 2 3; do tick; sleep 4; done
LEFT=$(q "SELECT count(*) FROM sidecar.payout WHERE state IN ('claimed','signed')")
[ "$LEFT" = 0 ] || { echo "ledger has $LEFT claimed/signed rows; see $LOG/tick.log"; exit 2; }

export RUN OUT
RESULTS=()
for s in "${SCENARIOS[@]}"; do
	T0=$(date +%s)
	LINE=$(bash "./$s.sh" 2> "$OUT/$s.stderr" | tail -1)
	RESULTS+=("$(printf '%-22s %-4s %4ss  %s' "$s" "${LINE%% *}" $(( $(date +%s) - T0 )) "$(sed -n 's/^FAIL [^:]*: //p' <<< "$LINE")")")
	echo "${RESULTS[-1]}"
done
echo; printf '%s\n' "${RESULTS[@]}"
printf '%s\n' "${RESULTS[@]}" | awk '$2 != "PASS" {bad=1} END {exit bad}'
