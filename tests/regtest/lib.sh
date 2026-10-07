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
BIN=${BIN:-$ROOT/target/debug/abandon-ship-server}
PROJECT=${COMPOSE_PROJECT_NAME:-abandon-regtest}
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
mine() {
	btc -rpcwallet=faucet -generate "${1:-1}" >/dev/null || exit 2
	captaind_synced
}
tip() { btc getblockcount; }
# Admin query (postgres superuser = captaind's DB owner): one value per line.
q() { "$R/psql" -At -F'|' -c "$1"; }
bark() { "$R/bark" "$@" 2>> "$LOG/bark.stderr"; }

# --- assertions ---------------------------------------------------------------

check() { # check <description> <command...>
	local d=$1; shift
	if "$@"; then say "ok: $d"; else say "NOT OK: $d"; FAILS+=("$d"); fi
}
eq() { [ "$1" = "$2" ] || { say "  got '$1', want '$2'"; return 1; }; }

# Ledger invariants (paid coins spent, journal) over the whole DB, then PASS/FAIL.
finish() {
	local bad
	bad=$(q "SELECT count(*) FROM sidecar.payout p LEFT JOIN vtxo v ON v.vtxo_id = p.vtxo_id
		WHERE v.vtxo_id IS NULL OR v.spend_state <> 'spent' OR v.spent_in_round IS NOT NULL
		   OR v.oor_spent_txid IS NOT NULL OR v.offboarded_in IS NOT NULL")
	check "every paid coin is spent and nothing else spent it" eq "$bad" 0
	local unjournaled=0 id
	for id in $(q "SELECT vtxo_id FROM sidecar.payout WHERE txid IS NOT NULL"); do
		grep -q "^$id " "$JOURNAL" || unjournaled=$((unjournaled + 1))
	done
	check "every ledger row with a txid is journaled" eq "$unjournaled" 0
	if [ ${#FAILS[@]} -eq 0 ]; then echo "PASS $NAME"; exit 0; fi
	echo "FAIL $NAME: $(IFS=';'; echo "${FAILS[*]}")"; exit 1
}

# --- sidecar ------------------------------------------------------------------

# mkcfg [key=value ...]: write $CFG from regtest/sidecar.toml with the test
# credentials, an absolute journal path, ban_wait_secs=3 (scenarios that race a
# round pass 45: above a 30 s round + 5 s submit + 5 s sign) and any overrides.
CFG=$LOG/sidecar.toml
mkcfg() {
	sed -e 's/^user = .*/user = "second"/' -e 's/^pass = .*/pass = "ark"/' \
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

# Tick <n> times, sleeping the ban wait after each.
ticks() { local i; for i in $(seq "$1"); do tick; sleep $(( $(ban_wait) + 1 )); done; }

# Mine 6 blocks and tick, so payouts reach the 'confirmed' state.
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

# Expiry tests drain the rounds wallet into watchmand's separate sweep wallet.
# Keep repeated suites funded from the regtest faucet.
ensure_round_funding() {
	local balance address info
	info=$("$R/captaind" rpc wallet | python3 -c 'import json,sys; w=json.load(sys.stdin)["rounds"]; print(w["trusted_balance"],w["address"])') || return 1
	read -r balance address <<< "$info"
	if [ "$balance" -lt 200000000 ]; then
		btc -rpcwallet=faucet -named sendtoaddress address="$address" amount=20 fee_rate=5 > /dev/null || return 1
		mine 2
	fi
}

# Wait for mined blocks before boarding or reading the ledger. Slow progress
# is allowed; ten minutes without progress fails setup.
captaind_synced() {
	local target height previous=-1 deadline=$((SECONDS + 600))
	target=$(tip) || exit 2
	while true; do
		height=$(q "SELECT coalesce(max(height), 0) FROM captaind_block") || exit 2
		[ "$height" -ge "$target" ] && return 0
		if [ "$height" -gt "$previous" ]; then
			previous=$height
			deadline=$((SECONDS + 600))
		fi
		(( SECONDS < deadline )) || break
		sleep 2
	done
	say "setup failed: captaind stalled at $height, waiting for $target"
	exit 2
}

# round <sat> <wallet>...: board <sat> per wallet, then refresh all of them
# into one round. Afterwards each wallet holds one round coin.
round() {
	local amt=$1; shift
	local w pids=()
	ensure_round_funding || { say "cannot fund captaind's rounds wallet"; exit 2; }
	mine 1
	for w in "$@"; do bark "$w" board "$amt sat" > "$LOG/board-$w.log" || { say "board failed: $w"; exit 2; }; done
	mine 4
	for w in "$@"; do bark "$w" balance > /dev/null; done
	for w in "$@"; do bark "$w" refresh --all > "$LOG/refresh-$w.log" & pids+=($!); done
	for w in "${pids[@]}"; do wait "$w" || { say "round setup failed"; exit 2; }; done
	mine 3
	for w in "$@"; do
		[ "$(coins "$w" | wc -w)" = 1 ] || { say "round setup: $w has no single spendable coin"; exit 2; }
	done
}

# Ids of a wallet's coins in a given client state (default spendable).
coins() { # coins <wallet> [state]
	bark "$1" vtxos | python3 -c "import json,sys
for v in json.load(sys.stdin):
    if v['state']['type'] == '${2:-spendable}': print(v['id'])"
}

# expired_coin <sat>: a fresh wallet $W whose one round coin $X (key $PK,
# amount $AMT) is expired and swept.
expired_coin() {
	W=$(wname a); newwallet "$W"
	round "$1" "$W"
	X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
	expire_and_sweep "$X" || finish
}

# Ark address of a wallet.
arkaddr() { bark "$1" address | tr -d '"[:space:]'; }

# Mine past expiry + grace of the coins, wait until the watchman swept every
# anchor (funding output) and the sweep is $SWEEP_CONFS deep.
expire_and_sweep() { # expire_and_sweep <ids>
	local ids=$1 inlist exp anchors a i spent
	[ -n "${ids// /}" ] || { check "coins to expire" false; return 1; }
	inlist=$(for i in $ids; do printf "'%s'," "$i"; done); inlist=${inlist%,}
	exp=$(q "SELECT max(expiry) FROM vtxo WHERE vtxo_id IN ($inlist)")
	anchors=$(q "SELECT DISTINCT anchor_point FROM vtxo WHERE vtxo_id IN ($inlist)")
	local h; h=$(tip)
	[ $((exp + GRACE + 1)) -gt "$h" ] && mine $((exp + GRACE + 1 - h))
	for i in $(seq 60); do
		spent=1
		for a in $anchors; do
			[ -n "$(anchor_spender "$a")" ] || spent=0
		done
		[ $spent = 1 ] && break
		mine 1; sleep 3
	done
	[ $spent = 1 ] || { check "anchors swept: $anchors" false; return 1; }
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
payout_address() { q "SELECT address FROM sidecar.payout WHERE vtxo_id='$1'"; }
spend_state() { q "SELECT spend_state FROM vtxo WHERE vtxo_id='$1'"; }
quarantine_reason() { q "SELECT reason FROM sidecar.quarantine WHERE vtxo_id='$1'"; }
bans() { q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id='$1'"; }
journaled() { grep -c "^$1 " "$JOURNAL"; }
anchor_of() { q "SELECT anchor_point FROM vtxo WHERE vtxo_id='$1'"; }
# Non-empty only when set.
anchor_spender() { q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$1' AND onchain_spent_txid IS NOT NULL"; }
leaf_confirmed() { q "SELECT confirmed_height FROM vtxo WHERE vtxo_id='$1' AND confirmed_height IS NOT NULL"; }
refreshed() { q "SELECT spent_in_round FROM vtxo WHERE vtxo_id='$1' AND spent_in_round IS NOT NULL"; }

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

# Check the complete batch before confirming it, including shared-key outputs.
assert_payout_fee() {
	local txid=$1
	btc getrawtransaction "$txid" 1 > "$LOG/fee-transaction.json" || exit 2
	btc getmempoolentry "$txid" > "$LOG/fee-mempool.json" || exit 2
	q "SELECT json_agg(p) FROM (SELECT address,sum(amount_sat) AS gross FROM sidecar.payout
		WHERE txid='$txid' GROUP BY address) p" > "$LOG/fee-entitlements.json" || exit 2
	check "recipients cover the entire mining fee, operator pays zero" python3 - "$LOG" <<'PY'
import json, sys
from decimal import Decimal
from pathlib import Path
p = Path(sys.argv[1])
def read(name):
    return json.loads((p / name).read_text(), parse_float=Decimal)
tx = read('fee-transaction.json')
deducted = 0
for entitlement in read('fee-entitlements.json'):
    outputs = [o for o in tx['vout'] if o['scriptPubKey'].get('address') == entitlement['address']]
    assert len(outputs) == 1
    net = int(outputs[0]['value'] * 100_000_000)
    assert net <= entitlement['gross']
    deducted += entitlement['gross'] - net
fee = int(read('fee-mempool.json')['fees']['base'] * 100_000_000)
assert deducted == fee, (deducted, fee)
print(f'recipient deductions={deducted} sat; mining fee={fee} sat; operator fee=0 sat')
PY
}

# coininfo <wallet> <id>: "<user_pubkey> <amount_sat>" from the wallet's view.
# Read it before the payout: a later sync may drop the coin from the list.
coininfo() {
	bark "$1" vtxos | python3 -c "import json,sys
v=[v for v in json.load(sys.stdin) if v['id'] == '$2'][0]; print(v['user_pubkey'], v['amount_sat'])"
}

# Assert one coin was paid exactly once, to tr(its key), for its amount minus
# at most its fee share (max_fee_pct_per_payout, default 20%).
assert_paid() { # assert_paid <id> <user_pubkey> <amount_sat> [max_fee_pct]
	local id=$1 pk=$2 amt=$3 pct=${4:-20} txid addr got
	txid=$(payout_txid "$id")
	check "${id:0:8} has a payout tx" test -n "$txid"
	[ -n "$txid" ] || return 0
	addr=$(tr_address "$pk")
	check "${id:0:8} payout address = tr(user key)" eq "$(payout_address "$id")" "$addr"
	got=$(paid_to "$txid" "$addr")
	check "${id:0:8} output within fee share of $amt (got $got)" test "$got" -le "$amt" -a "$got" -ge $((amt * (100 - pct) / 100))
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
		s=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$PROJECT-$1-1" 2>/dev/null)
		[ "$s" = healthy ] || [ "$s" = running ] && return 0
		sleep 1
	done
	return 1
}
