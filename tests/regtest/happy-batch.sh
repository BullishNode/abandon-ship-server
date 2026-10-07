#!/bin/bash
# Coins of three offline owners in one round are paid in one batched tx,
# one output each; the batch tx is journaled once.
. "$(dirname "$0")/lib.sh"
mkcfg
WS=(); for n in a b c; do WS+=("$(wname $n)"); newwallet "${WS[-1]}"; done
round 60000 "${WS[@]}"
IDS=(); INFO=()
for w in "${WS[@]}"; do IDS+=("$(coins "$w")"); INFO+=("$(coininfo "$w" "${IDS[-1]}")"); done
say "coins ${IDS[*]}"
check "three coins in one round" eq "$(q "SELECT count(DISTINCT anchor_point) FROM vtxo WHERE vtxo_id IN ('${IDS[0]}','${IDS[1]}','${IDS[2]}')")" 1
expire_and_sweep "${IDS[*]}" || finish

check "all paid within 3 ticks" pay_until "${IDS[*]}" 3
for i in 0 1 2; do read -r PK AMT <<< "${INFO[$i]}"; assert_paid "${IDS[$i]}" "$PK" "$AMT"; done
check "one batched tx" eq "$(q "SELECT count(DISTINCT txid) FROM sidecar.payout WHERE vtxo_id IN ('${IDS[0]}','${IDS[1]}','${IDS[2]}')")" 1
assert_payout_fee "$(payout_txid "${IDS[0]}")"
check "the batch tx is journaled once, not per coin" eq "$(grep -c " $(payout_txid "${IDS[0]}") [0-9a-f]" "$JOURNAL")" 1
confirm_payouts
check "batch confirmed" eq "$(q "SELECT string_agg(DISTINCT state, ',') FROM sidecar.payout WHERE vtxo_id IN ('${IDS[0]}','${IDS[1]}','${IDS[2]}')")" confirmed
finish
