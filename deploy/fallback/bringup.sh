#!/bin/bash
# First bring-up of the expiry-fallback stack, on the VM, from deploy/.
# Idempotent: each step checks whether it already ran. Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
dir=state/signet-fallback
dcf() { sudo ./fallback/dcf "$@"; }
q() { dcf exec -T fb-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d bark-server-db -Atc "$1"; }
healthy() { [ "$(sudo docker inspect -f '{{.State.Health.Status}}' "abandon-signet-fallback-$1-1" 2>/dev/null)" = healthy ]; }
wait_healthy() { for _ in $(seq 120); do healthy "$1" && return; sleep 2; done; echo "$1 not healthy" >&2; exit 1; }

./fallback/render.sh >/dev/null
# watchmand.toml must exist before captaind starts (it is mounted).
[ -f "$dir/watchmand.toml" ] || : > "$dir/watchmand.toml"
dcf up -d fb-postgres
wait_healthy fb-postgres

# captaind create: database, migrations and the server mnemonic (stays in its volume).
if ! dcf run --rm --no-deps -T --entrypoint sh fb-captaind -c 'test -f /data/captaind/mnemonic'; then
	dcf run --rm --no-deps -T fb-captaind create
fi
# The fork's schema additions, outside the numbered migrations (contrib/expiry-payout-task.md).
dcf exec -T fb-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d bark-server-db < fallback/sql/expiry-settlement.sql
dcf exec -T fb-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d bark-server-db < fallback/sql/expiry-fallback.sql

# Payouts stay off until watchmand sweeps into the rounds wallet.
if [ ! -f "$dir/sweep.env" ]; then
	dcf up -d fb-captaind
	wait_healthy fb-captaind
	addr=$(dcf exec -T fb-captaind captaind --config /config/captaind.toml rpc wallet \
		| python3 -c 'import json,sys; print(json.load(sys.stdin)["rounds"]["address"])')
	printf 'SWEEP_ADDRESS=%s\n' "$addr" > "$dir/sweep.env"
fi
./fallback/render.sh enabled >/dev/null
dcf up -d --force-recreate fb-captaind
wait_healthy fb-captaind
dcf up -d fb-watchmand fb-ark fb-barkd fb-api fb-web
dcf ps
