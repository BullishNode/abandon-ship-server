#!/bin/bash
# An address rotation leaves more than one page of old sweeps unconfigured.
# Those coins wait; a later coin swept to the configured address still pays.
. "$(dirname "$0")/lib.sh"
S=$(wname sender); T=$(wname waiting); B=$(wname big)
newwallet "$S"; newwallet "$T" 0
round 2000000 "$S"
for i in $(seq 21); do
	bark "$S" send "$(arkaddr "$T")" "50000 sat" >> "$LOG/send.log" || { check "send $i" false; finish; }
done
XS=$(coins "$T"); XC=$(coins "$S")
X=$(echo $XS | sed "s/ /','/g")
# Arkoor can split a 50k payment across several outputs. Count actual coins
# above the test's minimum, not the number or requested size of sends.
MIN=10000
check "more than one page of eligible-size waiting coins" test "$(q "SELECT count(*) FROM vtxo WHERE vtxo_id IN ('$X') AND amount >= $MIN")" -gt 20
[ ${#FAILS[@]} -eq 0 ] || finish
expire_and_sweep "$XS $XC" || finish

cp "$R/watchmand.toml" "$LOG/watchmand-original.toml"
restore_watchman() {
	cp "$LOG/watchmand-original.toml" "$R/watchmand.toml"
	(cd "$R" && docker compose restart watchmand > /dev/null 2>&1)
}
trap restore_watchman EXIT
NEW=$(btc -rpcwallet=faucet getnewaddress '' bech32m)
python3 - "$R/watchmand.toml" "$NEW" <<'PYEDIT'
from pathlib import Path
import re, sys
p=Path(sys.argv[1]); text,n=re.subn(r'^sweep_address = .*$', 'sweep_address = "'+sys.argv[2]+'"', p.read_text(), flags=re.M)
assert n == 1
p.write_text(text)
PYEDIT
(cd "$R" && docker compose restart watchmand > /dev/null 2>&1) || exit 2
check "watchmand loaded the rotated address" bash -c 'docker exec "$1-watchmand-1" cat /config/watchmand.toml | grep -qF "$2"' _ "$PROJECT" "$NEW"
newwallet "$B"; round 500000 "$B"
XB=$(coins "$B"); read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XB" || finish
mkcfg max_batch=1 min_payout_sat=$MIN "sweep_addresses=[\"$NEW\"]"
RATE=$(btc estimatesmartfee 6 | python3 -c 'import json,sys; print(json.load(sys.stdin)["feerate"]*100000)')
THRESHOLD=$(python3 - "$RATE" "$MIN" <<'PY'
import math,sys
share=math.ceil(float(sys.argv[1])*230)
print(max(int(sys.argv[2]),math.ceil(share*100/20),share+330))
PY
)
check "waiting page remains affordable at the actual estimate" test "$(q "SELECT count(*) FROM vtxo WHERE vtxo_id IN ('$X') AND amount >= $THRESHOLD")" -gt 20
[ ${#FAILS[@]} -eq 0 ] || finish
check "later eligible coin pays past waiting page" pay_until "$XB" 12
assert_paid "$XB" "$PKB" "$AMTB"
check "unconfigured old sweeps remain unclaimed" eq "$(q "SELECT count(*) FROM sidecar.payout WHERE vtxo_id IN ('$X')")" 0

# Restore the missing address and drain this scenario's remaining entitlements.
restore_watchman; trap - EXIT
mkcfg "sweep_addresses=[\"$NEW\",$(sed -n 's/^sweep_addresses = \[\(.*\)\]/\1/p' "$R/sidecar.toml")]"
check "old coins pay once their sweep address is configured" pay_until "$XS" 6
confirm_payouts
finish
