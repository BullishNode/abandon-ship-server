#!/bin/bash
# H2 probe: flip a coin to spent ~2s after captaind starts the round the
# owner is waiting for (inside the submit window, before persist).
cd "$(dirname "$0")"; W=$1; LOG=/tmp/claude-1000/h2-$W.log
./btc -rpcwallet=faucet -generate 2 >/dev/null; ./bark $W balance >/dev/null 2>&1
I=$(./bark $W vtxos 2>/dev/null | python3 -c "import json,sys;print([v['id'] for v in json.load(sys.stdin) if v['state']['type']=='spendable'][0])")
echo "coin ${I:0:12}"
rc(){ docker compose logs captaind 2>&1 | grep -c 'Round started'; }
rm -f $LOG; (./bark $W refresh --vtxo $I > $LOG 2>&1 &)
until grep -q 'Waiting for a round start' $LOG 2>/dev/null; do sleep 0.3; done
B=$(rc); until [ "$(rc)" -gt "$B" ]; do sleep 0.3; done; sleep ${2:-2}
./psql -At -c "UPDATE vtxo SET spend_state='spent', updated_at=NOW() WHERE vtxo_id='$I' AND spend_state='spendable' RETURNING 'flipped'"
timeout 90 sh -c "until grep -q -E 'funding_txid|An error occurred' $LOG; do sleep 1; done"
grep -E 'An error occurred|funding_txid' $LOG | cut -c1-230
./psql -At -F' ' -c "SELECT spend_state, spent_in_round FROM vtxo WHERE vtxo_id='$I'"
