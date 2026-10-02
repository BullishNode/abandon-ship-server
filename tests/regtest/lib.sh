#!/bin/bash
# Shared helpers for the regtest scenarios. Source it from a scenario script.
#
# Each scenario makes fresh bark wallets, puts coins in a round, mines to
# expiry, waits for the watchman sweep, runs sidecar ticks (`--once`) and
# asserts on Postgres, the chain and the journal. It ends with `finish`, which
# also checks the ledger invariants and prints PASS/FAIL.

set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
R=$ROOT/regtest
BIN=$ROOT/target/debug/abandon-ship-server
JOURNAL=$R/payouts.journal
NAME=$(basename "$0" .sh)
RUN=${RUN:-$(date +%s)}
OUT=${OUT:-/tmp/abandon-regtest}
LOG=$OUT/$NAME
mkdir -p "$LOG"
: > "$LOG/sidecar.log"
FAILS=()
GRACE=10
SWEEP_CONFS=6

say() { echo "[$NAME] $*" >&2; }
btc() { "$R/btc" "$@"; }
mine() { btc -rpcwallet=faucet -generate "${1:-1}" >/dev/null; }
tip() { btc getblockcount; }
# Admin query (postgres superuser = captaind's DB owner): one value per line.
q() { "$R/psql" -At -F'|' -c "$1"; }
bark() { "$R/bark" "$@" 2>/dev/null; }

# --- assertions ---------------------------------------------------------------

check() { # check <description> <command...>
	local d=$1; shift
	if "$@"; then say "ok: $d"; else say "NOT OK: $d"; FAILS+=("$d"); fi
}
eq() { [ "$1" = "$2" ] || { say "  got '$1', want '$2'"; return 1; }; }

# Ledger invariants (I2, journal) over the whole DB, then PASS/FAIL.
finish() {
	local bad
	bad=$(q "SELECT count(*) FROM sidecar.payout p LEFT JOIN vtxo v ON v.vtxo_id = p.vtxo_id
		WHERE v.vtxo_id IS NULL OR v.spend_state <> 'spent' OR v.spent_in_round IS NOT NULL
		   OR v.oor_spent_txid IS NOT NULL OR v.offboarded_in IS NOT NULL")
	check "I2: every paid coin is spent and nothing else spent it" eq "$bad" 0
	local unjournaled=0 id
	for id in $(q "SELECT vtxo_id FROM sidecar.payout WHERE txid IS NOT NULL"); do
		grep -q "^$id " "$JOURNAL" || unjournaled=$((unjournaled + 1))
	done
	check "every ledger row with a txid is journaled" eq "$unjournaled" 0
	if [ ${#FAILS[@]} -eq 0 ]; then echo "PASS $NAME"; exit 0; fi
	echo "FAIL $NAME: $(IFS=';'; echo "${FAILS[*]}")"; exit 1
}

# --- sidecar ------------------------------------------------------------------

# mkcfg [key=value ...]: write $CFG from regtest/sidecar.toml, with the
# whitelisted RPC user, an absolute journal path, a short ban wait (scenarios
# that race the round use ban_wait_secs=45, i.e. above round_interval 30s +
# submit 5s + sign 5s), and any overrides.
CFG=$LOG/sidecar.toml
mkcfg() {
	sed -e 's/^user = .*/user = "sidecar"/' -e 's/^pass = .*/pass = "sidecar-regtest"/' \
		-e "s#^journal_path = .*#journal_path = \"$JOURNAL\"#" \
		-e 's/^ban_wait_secs = .*/ban_wait_secs = 3/' "$R/sidecar.toml" > "$CFG"
	local kv
	for kv in "$@"; do
		sed -i "s#^${kv%%=*} = .*#${kv%%=*} = ${kv#*=}#" "$CFG"
		grep -qxF "${kv%%=*} = ${kv#*=}" "$CFG" || { say "mkcfg: no key ${kv%%=*}"; exit 2; }
	done
}
ban_wait() { sed -n 's/^ban_wait_secs = //p' "$CFG"; }

# One sidecar tick. Returns the process exit code; output in $LOG/tick.log
# (last tick) and $LOG/sidecar.log (all).
tick() {
	RUST_LOG=${RUST_LOG:-info} timeout "${TICK_TIMEOUT:-300}" "$BIN" "$CFG" --once > "$LOG/tick.log" 2>&1
	local rc=$?
	sed 's/\x1b\[[0-9;]*m//g' "$LOG/tick.log" | tee -a "$LOG/sidecar.log" > "$LOG/tick.clean"
	mv "$LOG/tick.clean" "$LOG/tick.log"
	return $rc
}

# Tick until every coin has a payout row in one of the states (default:
# broadcast or confirmed), sleeping the ban wait in between. Max $2 ticks.
pay_until() { # pay_until "<ids>" [max_ticks] [states]
	local ids=$1 max=${2:-6} states=${3:-"'broadcast','confirmed'"} i id done
	for i in $(seq "$max"); do
		tick || say "tick exited $?"
		done=1
		for id in $ids; do
			[ -n "$(q "SELECT 1 FROM sidecar.payout WHERE vtxo_id='$id' AND state IN ($states)")" ] || done=0
		done
		[ $done = 1 ] && return 0
		sleep $(( $(ban_wait) + 1 ))
	done
	return 1
}

# Mine and tick until the payout txs confirm (6 confs for the 'confirmed' state).
confirm_payouts() {
	mine 6
	tick || true
}

# --- wallets and coins --------------------------------------------------------

# newwallet <name> [btc]: fresh bark wallet with on-chain funds (default 0.5).
newwallet() {
	local w=$1 amt=${2:-0.5}
	"$R/bark" "$w" create --regtest --ark http://captaind:3535 --bitcoind http://bitcoind:18443 \
		--bitcoind-user second --bitcoind-pass ark --datadir "/wallets/$w" > "$LOG/create-$w.log" 2>&1 \
		|| { say "cannot create wallet $w"; exit 2; }
	if [ "$amt" != 0 ]; then
		local a; a=$(bark "$w" onchain address | sed -n 's/.*"address": "\(.*\)".*/\1/p')
		btc -rpcwallet=faucet -named sendtoaddress address="$a" amount="$amt" fee_rate=5 > /dev/null
	fi
}
wname() { echo "t-$NAME-$RUN-$1"; }

# round <sat> <wallet>...: board <sat> per wallet, then refresh all of them
# into one round. Afterwards each wallet holds one round coin.
round() {
	local amt=$1; shift
	local w pids=()
	mine 1
	for w in "$@"; do bark "$w" board "$amt sat" > "$LOG/board-$w.log" || say "board failed: $w"; done
	mine 4
	for w in "$@"; do bark "$w" balance > /dev/null; done
	for w in "$@"; do bark "$w" refresh --all > "$LOG/refresh-$w.log" & pids+=($!); done
	for w in "${pids[@]}"; do wait "$w"; done
	mine 3
	for w in "$@"; do bark "$w" balance > /dev/null; done
}

# Ids of a wallet's coins in a given client state (default spendable).
coins() { # coins <wallet> [state]
	bark "$1" vtxos | python3 -c "import json,sys
for v in json.load(sys.stdin):
    if v['state']['type'] == '${2:-spendable}': print(v['id'])"
}
vtxo_field() { # vtxo_field <wallet> <id> <field>
	bark "$1" vtxos | python3 -c "import json,sys
print([v for v in json.load(sys.stdin) if v['id'] == '$2'][0]['$3'])"
}

# Ark address of a wallet.
arkaddr() { bark "$1" address | tr -d '"[:space:]'; }

# Mine past expiry + grace of the coins, wait until the watchman swept every
# anchor (funding output) and the sweep is $SWEEP_CONFS deep.
expire_and_sweep() { # expire_and_sweep <ids>
	local ids=$1 inlist exp anchors a i spent
	inlist=$(for i in $ids; do printf "'%s'," "$i"; done); inlist=${inlist%,}
	exp=$(q "SELECT max(expiry) FROM vtxo WHERE vtxo_id IN ($inlist)")
	anchors=$(q "SELECT DISTINCT anchor_point FROM vtxo WHERE vtxo_id IN ($inlist)")
	local h; h=$(tip)
	[ $((exp + GRACE + 1)) -gt "$h" ] && mine $((exp + GRACE + 1 - h))
	for i in $(seq 60); do
		spent=1
		for a in $anchors; do
			[ -n "$(q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$a' AND onchain_spent_txid IS NOT NULL")" ] || spent=0
		done
		[ $spent = 1 ] && break
		mine 1; sleep 3
	done
	[ $spent = 1 ] || { say "anchors not swept: $anchors"; return 1; }
	mine $SWEEP_CONFS
	ensure_fees
}

# Mining hundreds of empty blocks empties bitcoind's fee estimator; refill it
# with fee-paying txs (no -fallbackfee anywhere, as in production).
ensure_fees() {
	btc estimatesmartfee 6 | grep -q '"feerate"' || "$R/seed-fees" 8 > /dev/null
	btc estimatesmartfee 6 | grep -q '"feerate"' || { say "fee estimator still empty"; return 1; }
}

# --- payout facts -------------------------------------------------------------

payout_state() { q "SELECT state FROM sidecar.payout WHERE vtxo_id='$1'"; }
payout_txid() { q "SELECT txid FROM sidecar.payout WHERE vtxo_id='$1'"; }
spend_state() { q "SELECT spend_state FROM vtxo WHERE vtxo_id='$1'"; }
quarantine_reason() { q "SELECT reason FROM sidecar.quarantine WHERE vtxo_id='$1'"; }
journaled() { grep -c "^$1 " "$JOURNAL"; }

# BIP86 tr(key) address derived by bitcoind from a compressed pubkey.
tr_address() {
	local d; d=$(btc getdescriptorinfo "tr(${1:2})" | sed -n 's/.*"descriptor": "\(.*\)".*/\1/p')
	btc deriveaddresses "$d" | sed -n 's/.*"\(bcrt1.*\)".*/\1/p'
}

# Sum of the outputs of <txid> paying <address>.
paid_to() {
	btc getrawtransaction "$1" 1 | python3 -c "import json,sys
t=json.load(sys.stdin); print(sum(round(o['value']*1e8) for o in t['vout'] if o['scriptPubKey'].get('address')=='$2'))"
}

# How many confirmed or mempool txs ever paid <address> (scantxoutset only sees
# unspent outputs, so use the payout wallet's view plus the tx index).
txs_paying() {
	q "SELECT DISTINCT txid FROM sidecar.payout WHERE address='$1' AND txid IS NOT NULL" | grep -c .
}

# coininfo <wallet> <id>: "<user_pubkey> <amount_sat>" from the wallet's view.
# Read it before the payout: a later sync may drop the coin from the list.
coininfo() {
	bark "$1" vtxos | python3 -c "import json,sys
v=[v for v in json.load(sys.stdin) if v['id'] == '$2'][0]; print(v['user_pubkey'], v['amount_sat'])"
}

# Assert one coin was paid exactly once, to tr(its key), for its amount minus
# at most its fee share (20%).
assert_paid() { # assert_paid <id> <user_pubkey> <amount_sat>
	local id=$1 pk=$2 amt=$3 txid addr got
	txid=$(payout_txid "$id")
	check "${id:0:8} has a payout tx" test -n "$txid"
	[ -n "$txid" ] || return 0
	addr=$(tr_address "$pk")
	check "${id:0:8} payout address = tr(user key)" eq "$(q "SELECT address FROM sidecar.payout WHERE vtxo_id='$id'")" "$addr"
	got=$(paid_to "$txid" "$addr")
	check "${id:0:8} output within fee share of $amt (got $got)" test "$got" -le "$amt" -a "$got" -ge $((amt * 80 / 100))
	check "${id:0:8} spent in captaind" eq "$(spend_state "$id")" spent
	check "${id:0:8} journaled once, with the ledger txid" eq "$(grep -cE "^$id $txid( |$)" "$JOURNAL")" 1
	check "${id:0:8} journaled nowhere else" eq "$(journaled "$id")" 1
}

# Payout-wallet UTXO lock: makes funding fail, so claims stay 'claimed'.
lock_wallet() {
	btc -rpcwallet=payout lockunspent false "$(btc -rpcwallet=payout listunspent 0 | python3 -c "import json,sys
print(json.dumps([{'txid':u['txid'],'vout':u['vout']} for u in json.load(sys.stdin)]))")" > /dev/null
}
unlock_wallet() { btc -rpcwallet=payout lockunspent true > /dev/null; }

# Txs the payout wallet ever sent to <address> (independent of the ledger).
wallet_sends_to() {
	btc -rpcwallet=payout listtransactions "*" 100000 | python3 -c "import json,sys
print(len({t['txid'] for t in json.load(sys.stdin) if t['category']=='send' and t.get('address')=='$1'}))"
}

# The sidecar as a long-running loop in the background; its exit code lands
# in $LOG/loop.rc.
start_loop() {
	rm -f "$LOG/loop.rc"
	( RUST_LOG=info "$BIN" "$CFG" > "$LOG/loop.log" 2>&1; echo $? > "$LOG/loop.rc" ) &
	sleep 3
}
stop_loop() { pkill -f "$BIN $CFG" || true; }
# Wait up to $1 s for the loop to exit; prints its exit code, or "running".
loop_exit() {
	local i
	for i in $(seq "$1"); do [ -s "$LOG/loop.rc" ] && { cat "$LOG/loop.rc"; return; }; sleep 1; done
	echo running
}
# Wait for a compose service to be healthy (or just running without a check).
healthy() {
	local i s
	for i in $(seq "${2:-120}"); do
		s=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "abandon-regtest-$1-1" 2>/dev/null)
		[ "$s" = healthy ] || [ "$s" = running ] && return 0
		sleep 1
	done
	return 1
}
