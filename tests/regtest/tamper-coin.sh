#!/bin/bash
# #17/#19/#81/#82/#84: a DB writer edits captaind's rows. Each coin is
# quarantined with the right reason and never paid:
# - vtxo.amount raised          -> claim refused (db amount != validated)
# - blob replaced by garbage    -> undecodable
# - blob of another coin        -> blob id != row id
# - anchor's spender = other tx -> recorded spender does not spend the anchor
# - anchor's spender not hex    -> unparseable spender txid (B1)
. "$(dirname "$0")/lib.sh"
mkcfg
for n in amt garb swap donor; do eval "W_$n=$(wname $n)"; newwallet "$(wname $n)"; done
WS1=$(wname spender1); WS2=$(wname spender2); newwallet "$WS1"; newwallet "$WS2"
round 60000 "$W_amt" "$W_garb" "$W_swap" "$W_donor"
round 60000 "$WS1"
round 60000 "$WS2"
AMT=$(coins "$W_amt"); GARB=$(coins "$W_garb"); SWAP=$(coins "$W_swap"); DONOR=$(coins "$W_donor")
SP1=$(coins "$WS1"); SP2=$(coins "$WS2")
expire_and_sweep "$AMT $GARB $SWAP $DONOR $SP1 $SP2" || finish

anchor() { q "SELECT anchor_point FROM vtxo WHERE vtxo_id='$1'"; }
A1=$(anchor "$SP1"); A2=$(anchor "$SP2")
OLD1=$(q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$A1'"); OLD2=$(q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$A2'")
OTHER=$(btc getblock "$(btc getbestblockhash)" | python3 -c "import json,sys;print(json.load(sys.stdin)['tx'][0])")
blob() { q "SELECT encode(vtxo, 'hex') FROM vtxo WHERE vtxo_id='$1'"; }
GARB_ORIG=$(blob "$GARB"); SWAP_ORIG=$(blob "$SWAP")
set_db() { q "UPDATE vtxo SET $2, updated_at=NOW() WHERE vtxo_id='$1'" > /dev/null; }
set_db "$AMT" "amount = amount + 1000"
set_db "$GARB" "vtxo = '\\xdeadbeef'::bytea"
set_db "$SWAP" "vtxo = (SELECT vtxo FROM vtxo WHERE vtxo_id='$DONOR')"
set_db "$A1" "onchain_spent_txid = '$OTHER'"
set_db "$A2" "onchain_spent_txid = 'not-a-txid'"

for i in 1 2 3; do tick; sleep $(( $(ban_wait) + 1 )); done
expect() { # expect <id> <reason pattern> <label>
	check "$3: quarantined ($(quarantine_reason "$1"))" grep -q "$2" <<< "$(quarantine_reason "$1")"
	check "$3: not paid" eq "$(payout_state "$1")" ""
	check "$3: still spendable in captaind" eq "$(spend_state "$1")" spendable
}
expect "$AMT" "db amount .* != validated" "amount edit"
expect "$GARB" "undecodable vtxo" "garbage blob"
expect "$SWAP" "blob id != row id" "swapped blob"
expect "$SP1" "does not spend the anchor" "unrelated spender"
expect "$SP2" "unparseable spender txid" "non-hex spender"
check "the untouched donor coin is paid" pay_until "$DONOR" 2

# Put captaind's rows back (they stay quarantined).
set_db "$AMT" "amount = amount - 1000"
set_db "$GARB" "vtxo = decode('$GARB_ORIG', 'hex')"
set_db "$SWAP" "vtxo = decode('$SWAP_ORIG', 'hex')"
set_db "$A1" "onchain_spent_txid = '$OLD1'"
set_db "$A2" "onchain_spent_txid = '$OLD2'"
confirm_payouts
finish
