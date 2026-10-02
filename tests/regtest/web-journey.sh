#!/bin/bash
# The user journey of bark-web on the variant-b barkd (image
# abandon-ship/bark:variant-b, as the compose service `barkd`): receive, go
# offline, expire, get paid, come back, adopt the spent state, find the payout,
# restore the seed on a second barkd, sweep, and check that the money shows once.
# Each barkd here is a fresh container with a fresh wallet, removed at the end.
. "$(dirname "$0")/lib.sh"
B=127.0.0.1:43010 B2=127.0.0.1:43011 NET=abandon-regtest_default
C=abandon-regtest-web-journey C2=abandon-regtest-web-journey-restore
api() { curl -s -X "$1" "$2$3" -H 'content-type: application/json' -d "${4:-{\}}"; } # api <method> <host> <path> [json]
jlen() { python3 -c "import json,sys; print(len(json.load(sys.stdin)))"; }
barkd() { # barkd <container> <host:port>
	docker rm -f "$1" > /dev/null 2>&1
	docker run -d --name "$1" --network $NET -p "$2:3000" abandon-ship/bark:variant-b \
		barkd --host 0.0.0.0 --port 3000 --dangerously-allow-remote-no-auth > /dev/null
	local i; for i in $(seq 30); do api GET "$2" /ping | grep -q . && return; sleep 1; done
}
create() { # create <host:port> [json fields]: regtest wallet on our captaind and bitcoind
	api POST "$1" /api/v1/wallet/create "{\"network\":\"regtest\",\"ark_server\":\"http://captaind:3535\",${2:-}
		\"chain_source\":{\"bitcoind\":{\"bitcoind\":\"http://bitcoind:18443\",\"bitcoind_auth\":{\"user-pass\":{\"user\":\"second\",\"pass\":\"ark\"}}}}}" > /dev/null
}
up() { local i; for i in $(seq 60); do api GET "$1" /api/v1/wallet/balance | grep -q spendable && return 0; sleep 1; done; return 1; }
expiry_movements() { api GET "$1" /api/v1/wallet/movements | python3 -c "import json,sys
print(sum(m['subsystem']['kind'] == 'expiry-payout' for m in json.load(sys.stdin)))"; }

mkcfg
BIRTH=$(tip)
barkd $C $B; create $B; up $B || { say "no barkd wallet (image abandon-ship/bark:variant-b?)"; finish; }
A=$(api POST $B /api/v1/onchain/addresses/next | python3 -c "import json,sys;print(json.load(sys.stdin)['address'])")
btc -rpcwallet=faucet -named sendtoaddress address="$A" amount=0.01 fee_rate=5 > /dev/null; mine 1; api POST $B /api/v1/onchain/sync > /dev/null

# Receive: board, then refresh into a round.
captaind_synced; api POST $B /api/v1/boards/board-amount '{"amount_sat":100000}' > "$LOG/board.json"; mine 4; sleep 3
api POST $B /api/v1/wallet/sync > /dev/null
api POST $B /api/v1/wallet/refresh/all > "$LOG/refresh.json"
for i in $(seq 40); do api GET $B /api/v1/wallet/rounds | grep -q '"funding_txid":"' && break; sleep 3; done
mine 3; sleep 3; api POST $B /api/v1/wallet/sync > /dev/null
ID=$(api GET $B /api/v1/wallet/vtxos | python3 -c "import json,sys
print(*[v['id'] for v in json.load(sys.stdin) if v['state']['type']=='spendable'])")
check "one round coin" eq "$(wc -w <<< "$ID")" 1
AMT=$(q "SELECT amount FROM vtxo WHERE vtxo_id='$ID'")
say "coin $ID ($AMT sat)"

# Offline through expiry, sweep and payout broadcast.
docker stop $C > /dev/null
expire_and_sweep "$ID" || finish
check "payout broadcast" pay_until "$ID" 6 "'broadcast'"
TXID=$(payout_txid "$ID")

# Back online, payout in the mempool (J1).
docker start $C > /dev/null; up $B
check "J1: server spent state adopted" eq "$(api POST $B /api/v1/wallet/vtxos/adopt-server-status "{\"vtxo_ids\":[\"$ID\"]}" | grep -o '"state":"[a-z]*"')" '"state":"spent"'
check "J1: coin marked spent in the wallet" eq "$(api GET $B /api/v1/wallet/vtxos/$ID | python3 -c "import json,sys;print(json.load(sys.stdin)['state']['type'])")" spent
say "J1: payouts seen while the payout is in the mempool: $(api POST $B /api/v1/wallet/vtxos/expiry-payouts | jlen) (bitcoind chain source: confirmed only)"

# Confirmed: Paid out.
mine 1
P=$(api POST $B /api/v1/wallet/vtxos/expiry-payouts "{\"vtxo_ids\":[\"$ID\"]}")
check "payout found after 1 conf" eq "$(echo "$P" | jlen)" 1
check "found payout is the ledger tx" grep -q "$TXID" <<< "$P"

# J6: restore the seed on a second barkd before the sweep.
# The mnemonic goes from one container to the other, never to the terminal.
barkd $C2 $B2
create $B2 "\"birthday_height\":$BIRTH,\"mnemonic\":\"$(docker exec $C cat /root/.bark/mnemonic | tr -d '\n')\","
up $B2 || say "restored barkd has no wallet"; api POST $B2 /api/v1/wallet/sync > /dev/null
check "J6: restored wallet has the coin as spent" eq "$(api GET $B2 /api/v1/wallet/vtxos/$ID | python3 -c "import json,sys;print(json.load(sys.stdin)['state']['type'])")" spent
check "J6: restored wallet finds the payout" grep -q "$TXID" <<< "$(api POST $B2 /api/v1/wallet/vtxos/expiry-payouts)"

# Sweep (J3, J4).
M0=$(expiry_movements $B)
S=$(api POST $B /api/v1/onchain/sweep-expiry-payouts | tee "$LOG/sweep1.json")
SW=$(echo "$S" | python3 -c "import json,sys;print(json.load(sys.stdin)['txid'])" 2>/dev/null)
check "sweep broadcast" test -n "$SW"
SW=${SW:-no-sweep}
api POST $B /api/v1/wallet/vtxos/expiry-payouts > "$LOG/payouts-after-sweep.json"
check "J3: payout no longer listed while the sweep is in the mempool" eq "$(jlen < "$LOG/payouts-after-sweep.json")" 0
api POST $B /api/v1/onchain/sweep-expiry-payouts '{"fee_rate_sat_vb":50}' > "$LOG/sweep2.json"
check "J4: second sweep refused (nothing to sweep)" grep -q "no expiry payouts" "$LOG/sweep2.json"
check "J4: one expiry-payout movement after a second sweep request" eq "$(expiry_movements $B)" $((M0 + 1))
check "J4: sweep still in the mempool, not replaced" grep -q "$SW" <<< "$(btc getrawmempool)"
mine 1
api POST $B /api/v1/onchain/sync > /dev/null
check "swept amount in the on-chain wallet" grep -q "$SW" <<< "$(api GET $B /api/v1/onchain/transactions)"

# J7: the restored wallet sees the swept money on-chain.
api POST $B2 /api/v1/onchain/sync > /dev/null
check "J7: restored wallet sees the sweep" grep -q "$SW" <<< "$(api GET $B2 /api/v1/onchain/transactions)"
docker rm -f $C $C2 > /dev/null
finish
