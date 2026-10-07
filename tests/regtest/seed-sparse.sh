#!/bin/bash
# Seed-only recovery of the payout script at sparse indexes, including reuse.
# These outputs are funded directly; unclaimed-delegated covers sidecar payment.
. "$(dirname "$0")/lib.sh"
mkcfg
W=$(wname seed); newwallet "$W" 0
M=$(cd "$R" && docker compose --profile cli run --rm -T --entrypoint cat bark "/wallets/$W/mnemonic" 2>/dev/null)
DESC=$(cd "$ROOT" && printf '%s\n' "$M" | cargo run -q --offline --example coin_key_descriptor -- regtest)
unset M
checksum() { btc getdescriptorinfo "$1" | python3 -c "import json,sys;print(json.load(sys.stdin)['checksum'])"; }
DESC="$DESC#$(checksum "$DESC")"
INDEXES=(0 257 65537 999999)
TXIDS=()
for i in "${INDEXES[@]}" 999999; do
	A=$(btc deriveaddresses "$DESC" "[$i,$i]" | python3 -c 'import json,sys;print(json.load(sys.stdin)[0])')
	TXIDS+=("$(btc -rpcwallet=faucet -named sendtoaddress address="$A" amount=0.0001 fee_rate=5)")
done
check "five funding transactions accepted" test "${#TXIDS[@]}" -eq 5
[ ${#FAILS[@]} -eq 0 ] || finish
mine 1

# A former 0..200 recovery range misses four of the five outputs.
btc scantxoutset start "[{\"desc\":\"$DESC\",\"range\":[0,200]}]" > "$LOG/narrow.json"
check "narrow scan finds only index zero" eq "$(python3 -c "import json;print(len(json.load(open('$LOG/narrow.json'))['unspents']))")" 1

# Measure the server doing the scan, not the short-lived bitcoin-cli process.
CORE_PID=$(docker inspect -f '{{.State.Pid}}' "$PROJECT-bitcoind-1")
check "RSS sample tracks bitcoind" grep -qx bitcoind "/proc/$CORE_PID/comm"
rss() { awk '/VmRSS:/ {print $2}' "/proc/$CORE_PID/status"; }
BASE_RSS=$(rss)
CORE_VERSION=$(btc getnetworkinfo | python3 -c 'import json,sys;print(json.load(sys.stdin)["subversion"])')
START_NS=$(date +%s%N)
btc -rpcclienttimeout=0 scantxoutset start "[{\"desc\":\"$DESC\",\"range\":[0,999999]}]" > "$LOG/scan.json" & SCAN=$!
while kill -0 "$SCAN" 2>/dev/null; do rss; sleep 0.1; done > "$LOG/rss-kib.txt"
wait "$SCAN"; SCAN_RC=$?
END_NS=$(date +%s%N)
check "million-index scan completed" test "$SCAN_RC" -eq 0
[ ${#FAILS[@]} -eq 0 ] || finish
check "all five outputs recovered, including key reuse" python3 - "$LOG/scan.json" "${TXIDS[@]}" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
assert s['success'] and len(s['unspents']) == 5
assert {u['txid'] for u in s['unspents']} == set(sys.argv[2:])
assert round(s['total_amount']*100_000_000) == 50_000
PY
python3 - "$LOG" "$BASE_RSS" "$START_NS" "$END_NS" "$CORE_VERSION" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); s=json.loads((p/'scan.json').read_text())
report=dict(range=[0,999999], funded_indexes=[0,257,65537,999999,999999],
    core_version=sys.argv[5], height=s['height'], bestblock=s['bestblock'],
    txouts_scanned=s['txouts'], matched_outputs=len(s['unspents']),
    elapsed_seconds=(int(sys.argv[4])-int(sys.argv[3]))/1e9,
    core_rss_before_kib=int(sys.argv[2]),
    core_peak_rss_kib=max(map(int,(p/'rss-kib.txt').read_text().split())))
(p/'measurement.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
PY

# The scan's public descriptors identify the matched derivation indexes.
# Import only those fixed private descriptors into a plain Core wallet.
FOUND=$(python3 - "$LOG/scan.json" <<'PY'
import json,re,sys
indices=set()
for u in json.load(open(sys.argv[1]))['unspents']:
    m=re.search(r'/([0-9]+)\]',u['desc'])
    assert m, u['desc']
    indices.add(int(m[1]))
print(*sorted(indices))
PY
)
check "matched descriptors locate the sparse indexes" eq "$FOUND" '0 257 65537 999999'
RW=seed-sparse-$RUN
btc -named createwallet wallet_name="$RW" blank=true load_on_startup=false > /dev/null
trap 'btc unloadwallet "$RW" > /dev/null' EXIT
IMPORTS=
for i in $FOUND; do
	FIXED=${DESC%%#*}; FIXED=${FIXED/\*/$i}
	FIXED="$FIXED#$(checksum "$FIXED")"
	IMPORTS+="{\"desc\":\"$FIXED\",\"timestamp\":0},"
done
printf '[%s]\n' "${IMPORTS%,}" | btc -rpcwallet="$RW" -stdin importdescriptors > "$LOG/import.json"
unset DESC FIXED IMPORTS
check "private descriptors imported" python3 -c "import json;assert all(r['success'] for r in json.load(open('$LOG/import.json')))"
SW=$(btc -rpcwallet="$RW" -named sendall recipients="[\"$(btc -rpcwallet=faucet getnewaddress)\"]" fee_rate=5 | python3 -c 'import json,sys;print(json.load(sys.stdin)["txid"])')
mine 1
check "Core spent exactly the five recovered outpoints" python3 - "$R/btc" "$SW" "$LOG/scan.json" <<'PY'
import json, subprocess, sys
t = json.loads(subprocess.check_output([sys.argv[1], 'getrawtransaction', sys.argv[2], '1']))
found = json.load(open(sys.argv[3]))['unspents']
assert t['confirmations'] >= 1 and len(t['vin']) == len(found) == 5
assert {(i['txid'], i['vout']) for i in t['vin']} == {(o['txid'], o['vout']) for o in found}
PY
finish
