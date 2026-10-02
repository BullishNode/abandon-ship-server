#!/bin/bash
# A delegated refresh whose owner never returns leaves an 'unclaimed'
# hArk output. It is paid, and the payout is spendable with the owner's seed
# (examples/coin_key_descriptor.rs).
. "$(dirname "$0")/lib.sh"
mkcfg
D=$(wname d); newwallet "$D"
mine 1
bark "$D" board "150000 sat" > "$LOG/board.log" || say "board failed"
mine 4
B=$(coins "$D")
say "board coin $B; delegated refresh, then the owner goes away"
bark "$D" refresh --delegated --all > "$LOG/delegated.log" 2>&1 || say "delegated refresh failed"
for i in $(seq 40); do
	RID=$(refreshed "$B")
	[ -n "$RID" ] && break; sleep 3
done
check "delegated refresh ran in a round" test -n "$RID"
[ -n "$RID" ] || finish
mine 3
FUND=$(q "SELECT funding_txid FROM round WHERE id=$RID")
ID=$(q "SELECT vtxo_id FROM vtxo WHERE anchor_point LIKE '$FUND:%' AND policy_type='pubkey' AND spend_state='unclaimed' AND amount > 1000")
check "one unclaimed output" test "$(echo "$ID" | grep -c .)" = 1
AMT=$(q "SELECT amount FROM vtxo WHERE vtxo_id='$ID'")
say "unclaimed output $ID ($AMT sat)"
expire_and_sweep "$ID" || finish

check "paid within 3 ticks" pay_until "$ID" 3
check "not quarantined" eq "$(quarantine_reason "$ID")" ""
TXID=$(payout_txid "$ID"); ADDR=$(payout_address "$ID")
GOT=$(paid_to "$TXID" "$ADDR")
check "output within fee share of $AMT (got $GOT)" test "$GOT" -le "$AMT" -a "$GOT" -ge $((AMT * 80 / 100))
confirm_payouts

# The owner comes back with only the seed: derive tr(xprv/350'/0'/*), find the
# payout, sweep it to the faucet.
M=$(cd "$R" && docker compose --profile cli run --rm -T --entrypoint cat bark "/wallets/$D/mnemonic" 2>/dev/null)
DESC=$(cd "$ROOT" && echo "$M" | cargo run -q --example coin_key_descriptor -- regtest 2>/dev/null)
check "descriptor from the seed" grep -q "^tr(tprv" <<< "$DESC"
DESC=$(btc getdescriptorinfo "$DESC" | python3 -c "import json,sys;d=json.load(sys.stdin);print('$DESC#'+d['checksum'])")
RW=recover-$RUN
btc -named createwallet wallet_name="$RW" blank=true load_on_startup=false > /dev/null
btc -rpcwallet="$RW" importdescriptors "[{\"desc\":\"$DESC\",\"range\":[0,200],\"timestamp\":0,\"active\":false}]" > "$LOG/import.log"
BAL=$(btc -rpcwallet="$RW" listunspent | python3 -c "import json,sys
print(sum(round(u['amount']*1e8) for u in json.load(sys.stdin) if u['txid']=='$TXID'))")
check "seed wallet sees the payout ($BAL sat)" eq "$BAL" "$GOT"
DEST=$(btc -rpcwallet=faucet getnewaddress)
SW=$(btc -rpcwallet="$RW" -named sendall recipients="[\"$DEST\"]" fee_rate=5 | python3 -c "import json,sys;print(json.load(sys.stdin)['txid'])")
mine 1
check "seed wallet swept it on-chain" test "$(btc getrawtransaction "$SW" 1 | python3 -c "import json,sys;print(json.load(sys.stdin).get('confirmations',0))")" -ge 1
btc unloadwallet "$RW" > /dev/null
finish
