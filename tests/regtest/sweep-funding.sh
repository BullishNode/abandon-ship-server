#!/bin/bash
# Route watchmand sweeps into the payout wallet. With all its old UTXOs
# locked, newly swept funds must pay the expired coin without a top-up.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname a); newwallet "$W"; round 60000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
ADDRESS=$(btc -rpcwallet=payout getnewaddress watchmand bech32m)
check "sweep address belongs to the payout wallet" python3 -c "import json,subprocess
d=json.loads(subprocess.check_output(['$R/btc','-rpcwallet=payout','getaddressinfo','$ADDRESS']))
assert d['ismine']"
cp "$R/watchmand.toml" "$LOG/watchmand-original.toml"
# Restore only this scenario's config and reservations, including on failure.
btc -rpcwallet=payout listunspent 0 | python3 -c 'import json,sys;print(json.dumps([dict(txid=u["txid"],vout=u["vout"]) for u in json.load(sys.stdin)]))' > "$LOG/locked.json"
cleanup() {
	btc -rpcwallet=payout lockunspent true "$(cat "$LOG/locked.json")" > /dev/null
	cp "$LOG/watchmand-original.toml" "$R/watchmand.toml"
	(cd "$R" && docker compose restart watchmand > /dev/null 2>&1)
}
trap cleanup EXIT
python3 - "$R/watchmand.toml" "$ADDRESS" <<'EDIT'
import re,sys
from pathlib import Path
p=Path(sys.argv[1]); s,n=re.subn(r'^sweep_address = .*$', 'sweep_address = "'+sys.argv[2]+'"',p.read_text(),flags=re.M)
assert n==1
p.write_text(s)
EDIT
(cd "$R" && docker compose restart watchmand > /dev/null 2>&1) || exit 2
check "watchmand restarted with the payout address" healthy watchmand
mkcfg "sweep_addresses=[\"$ADDRESS\"]"
btc -rpcwallet=payout lockunspent false "$(cat "$LOG/locked.json")" > /dev/null || exit 2
expire_and_sweep "$X" || finish
check "new sweep funds pay the coin" pay_until "$X" 4
assert_paid "$X" "$PK" "$AMT"
TX=$(payout_txid "$X")
check "every payout input came from the new sweep address" python3 - "$R/btc" "$TX" "$ADDRESS" <<'PY'
import json,subprocess,sys
def tx(txid): return json.loads(subprocess.check_output([sys.argv[1],'getrawtransaction',txid,'1']))
payout=tx(sys.argv[2])
assert payout['vin']
for i in payout['vin']:
    prev=tx(i['txid'])['vout'][i['vout']]
    assert prev['scriptPubKey'].get('address') == sys.argv[3]
PY
confirm_payouts
finish
