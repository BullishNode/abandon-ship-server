#!/bin/bash
# A wallet retained before a delegated refresh can still hold its old exit
# transactions. Paying the unclaimed replacement must not pay that value twice.
. "$(dirname "$0")/lib.sh"
mkcfg
INPUT_COUNT=${1:-1}
[[ "$INPUT_COUNT" = 1 || "$INPUT_COUNT" = 2 ]] || exit 2
D=$(wname delegated); E=$(wname saved)
newwallet "$D"
mine 1
for i in $(seq "$INPUT_COUNT"); do
	bark "$D" board '150000 sat' >> "$LOG/board.log" || exit 2
	mine 4
done
mapfile -t INPUTS < <(coins "$D")
check "$INPUT_COUNT original inputs" eq "${#INPUTS[@]}" "$INPUT_COUNT"
[ ${#FAILS[@]} -eq 0 ] || finish
OLD=${INPUTS[0]}
(cd "$R" && docker compose --profile cli run --rm -T --entrypoint sh bark \
	-c 'cp -a "/wallets/$1" "/wallets/$2"' _ "$D" "$E") > "$LOG/copy.log" 2>&1 || exit 2
bark "$D" refresh --delegated --all > "$LOG/delegated.log" 2>&1 || exit 2
RID=
for i in $(seq 40); do
	RID=$(refreshed "$OLD")
	[ -n "$RID" ] && break
	sleep 3
done
[ -n "$RID" ] || { check "delegated round funded" false; finish; }
mine 3
FUND=$(q "SELECT funding_txid FROM round WHERE id=$RID")
NEW=$(q "SELECT vtxo_id FROM vtxo WHERE anchor_point LIKE '$FUND:%'
	AND policy_type='pubkey' AND spend_state='unclaimed' AND amount>1000")
[ "$(echo "$NEW" | wc -w)" = 1 ] || exit 2
check "original input has no signed forfeit" eq "$(q "SELECT count(*) FROM round_part_input WHERE vtxo_id='$OLD' AND signed_forfeit_tx IS NOT NULL")" 0
check "replacement remains unclaimed" eq "$(spend_state "$NEW")" unclaimed

# The CLI syncs with captaind before starting an exit, which would finish
# the delegated forfeit. Model the saved wallet returning while Ark is down;
# the exit itself only needs Bitcoin. Restore the server before mining.
restart_captaind() {
	(cd "$R" && docker compose start captaind > /dev/null 2>&1)
}
trap restart_captaind EXIT
(cd "$R" && docker compose stop captaind > /dev/null 2>&1) || exit 2
bark "$E" exit start --vtxo "$OLD" > "$LOG/exit-start.log" 2>&1 || exit 2
restart_captaind || exit 2
trap - EXIT
captaind_synced
check "starting the saved wallet exit did not complete forfeits" eq "$(q "SELECT count(*) FROM round_part_input WHERE vtxo_id='$OLD' AND signed_forfeit_tx IS NOT NULL")" 0
for i in $(seq 30); do
	[ -n "$(leaf_confirmed "$OLD")" ] && break
	bark "$E" exit progress >> "$LOG/exit-progress.log" 2>&1 || exit 2
	mine 1
done
check "old user leaf confirmed before expiry" test -n "$(leaf_confirmed "$OLD")"
[ -n "$(leaf_confirmed "$OLD")" ] || finish
mine 16
bark "$E" exit progress >> "$LOG/exit-progress.log" 2>&1 || exit 2
DEST=$(btc -rpcwallet=faucet getnewaddress)
bark "$E" exit claim "$DEST" --vtxo "$OLD" --no-sync > "$LOG/claim.log" 2>&1 || exit 2
CLAIM=$(btc gettxspendingprevout "[{\"txid\":\"${OLD%:*}\",\"vout\":${OLD##*:}}]" \
	| python3 -c 'import json,sys; print(json.load(sys.stdin)[0].get("spendingtxid",""))')
check "owner spent the old exit output" test -n "$CLAIM"
[ -n "$CLAIM" ] || finish
mine 1
check "owner exit spend confirmed" test "$(btc getrawtransaction "$CLAIM" 1 | python3 -c 'import json,sys; print(json.load(sys.stdin).get("confirmations",0))')" -ge 1
check "replacement still has no completed forfeit" eq "$(spend_state "$NEW")" unclaimed
expire_and_sweep "$NEW" || finish
if [ "$INPUT_COUNT" = 2 ]; then
	check "other original input was swept" test -n "$(anchor_spender "$(anchor_of "${INPUTS[1]}")")"
fi
ticks 3
check "exited predecessor prevents replacement payout" eq "$(payout_txid "$NEW")" ''
check "replacement was not irreversibly claimed" eq "$(payout_state "$NEW")" ''
printf '%s\n' "old=$OLD" "replacement=$NEW" "owner_exit_claim=$CLAIM" "replacement_payout=$(payout_txid "$NEW")" > "$LOG/settlements.txt"
confirm_payouts
finish
