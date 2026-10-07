#!/bin/bash
# Actual pre-claim pg_restore with captaind launched before reconciliation.
# Sidecar runs on the host, as in the other regtest cases. A compose override
# installs the production gate into the stock captaind container for this case.
. "$(dirname "$0")/lib.sh"
GATE_ROOT=${GATE_ROOT:-$ROOT}
mkcfg
q "CREATE TABLE IF NOT EXISTS sidecar.reassert (id integer PRIMARY KEY CHECK (id=1), completed_at timestamptz NOT NULL)" > /dev/null || exit 2
expired_coin 60000
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/before.dump" || exit 2
check "fixture paid" pay_until "$X" 3
TX=$(payout_txid "$X"); [ -n "$TX" ] || finish
confirm_payouts
RAW=$(journal_raw "$X")
ADDRESS=$(tr_address "$PK")
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/current.dump" || exit 2
ORIGINAL_COMPOSE=${COMPOSE_FILE:-$R/compose.yaml}
STOPPED=0
cleanup() {
	[ "$STOPPED" = 1 ] || return 0
	(cd "$R" && docker compose stop captaind watchmand > /dev/null) || return 1
	export COMPOSE_FILE=$ORIGINAL_COMPOSE
	(cd "$R" && docker compose start bitcoind > /dev/null) || return 1
	healthy bitcoind || return 1
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/current.dump" > "$LOG/cleanup.log" 2>&1 || return 1
	tick || return 1
	(cd "$R" && docker compose up -d --no-deps captaind > /dev/null) || return 1
	healthy captaind || return 1
	(cd "$R" && docker compose start watchmand > /dev/null) || return 1
	STOPPED=0
}
trap 'cleanup || echo "SETUP FAIL: restore current.dump and original compose before restarting captaind" >&2' EXIT
python3 - "$LOG/gate.compose.json" "$GATE_ROOT" "$JOURNAL" <<'PY'
import json, pathlib, sys
path, root, journal = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({"services": {"captaind": {
    "environment": {"ABANDON_SHIP_SKIP_REASSERT_WAIT": "0",
        "ABANDON_SHIP_JOURNAL": "/journal/payouts.journal", "PGHOST": "postgres",
        "PGPORT": "5432", "PGUSER": "postgres", "PGPASSWORD": "abandon-regtest",
        "PGDATABASE": "bark-server-db"},
    "volumes": [root + "/regtest/scripts:/scripts:ro",
                journal + ":/journal/payouts.journal:ro"]}}}))
PY
export COMPOSE_FILE=$ORIGINAL_COMPOSE:$LOG/gate.compose.json

restore_before() {
	(cd "$R" && docker compose stop captaind watchmand > /dev/null) || return 1
	STOPPED=1
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/before.dump" > "$LOG/restore.log" 2>&1 || return 1
	eq "$(spend_state "$X")|$(payout_state "$X")" 'spendable|' || return 1
	q "INSERT INTO sidecar.reassert VALUES (1,'2000-01-01') ON CONFLICT (id) DO UPDATE SET completed_at=EXCLUDED.completed_at" > /dev/null
}
gate_log() {
	docker logs --since "$STARTED" "$PROJECT-captaind-1" > "$LOG/gate-$1.log" 2>&1
}
restore_before || exit 2
(cd "$R" && docker compose up -d --no-deps captaind > /dev/null) || exit 2
STARTED=$(docker inspect -f '{{.State.StartedAt}}' "$PROJECT-captaind-1")
sleep 2
gate_log stale
check "captaind waits despite a restored marker" grep -q 'Waiting for abandon-ship' "$LOG/gate-stale.log"
check "captaind has not opened its admin service" bash -c '! "$1" rpc wallet >/dev/null 2>&1' _ "$R/captaind"
check "reconciliation succeeds with captaind waiting" tick
check "coin excluded before serving clients" eq "$(spend_state "$X")" spent
check "captaind starts after the fresh pass" healthy captaind 30
gate_log fresh
check "startup reports fresh reconciliation" grep -q 'journal reassertion complete' "$LOG/gate-fresh.log"
# The CLI may first adopt the server's spent state or submit a refused round.
# Either path must leave the entitlement without a replacement round.
bark "$W" refresh --all > "$LOG/refresh-after-start.log" 2>&1 || true
check "owner gets no replacement round after restart" test -z "$(refreshed "$X")"
check "original payout remains the only payment" eq "$(wallet_sends_to "$ADDRESS")" 1

restore_before || exit 2
(cd "$R" && docker compose stop -t 600 bitcoind > /dev/null) || exit 2
(cd "$R" && docker compose up -d --no-deps captaind > /dev/null) || exit 2
STARTED=$(docker inspect -f '{{.State.StartedAt}}' "$PROJECT-captaind-1")
sleep 2
RC=0; tick || RC=$?
check "Core outage still reports the failed payment tick" test "$RC" -ne 0
check "reassertion completes without Core" eq "$(spend_state "$X")" spent
for i in $(seq 10); do
	gate_log core-down
	grep -q 'journal reassertion complete' "$LOG/gate-core-down.log" && break
	sleep 1
done
check "Core outage does not hold the reassert gate" grep -q 'journal reassertion complete' "$LOG/gate-core-down.log"
# Stock captaind still needs Core for its own startup. It may restart and
# request another marker; continue sidecar ticks while that dependency returns.
(cd "$R" && docker compose start bitcoind > /dev/null) || exit 2
healthy bitcoind || exit 2
for i in $(seq 10); do tick; healthy captaind 2 && break; done
check "captaind becomes healthy after Core returns" healthy captaind 30
check "outage recovery preserves original signed bytes" eq "$(journal_raw "$X")" "$RAW"
check "outage recovery does not duplicate payout" eq "$(wallet_sends_to "$ADDRESS")" 1
cleanup || exit 2
finish
