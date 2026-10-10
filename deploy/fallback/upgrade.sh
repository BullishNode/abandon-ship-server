#!/bin/bash
# Upgrade the running expiry-fallback stack to the images in signet-fallback.env,
# on the VM, from deploy/. Volumes are kept. Prints no secrets.
#   ./fallback/upgrade.sh server    stop every fork process, apply the schema, start captaind + watchmand
#   ./fallback/upgrade.sh wallets   start the Ark origin, web wallets and UIs on the new images
# One captaind per database: the old captaind stops before the schema additions
# and before the new binary starts. barkd stops with it, since an older client's
# records are refused by the new server.
set -euo pipefail
cd "$(dirname "$0")/.."
dcf() { sudo ./fallback/dcf "$@"; }
healthy() { [ "$(sudo docker inspect -f '{{.State.Health.Status}}' "abandon-signet-fallback-$1-1" 2>/dev/null)" = healthy ]; }
wait_healthy() { for _ in $(seq 120); do healthy "$1" && return; sleep 2; done; echo "$1 not healthy" >&2; exit 1; }
psql_file() { dcf exec -T fb-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d bark-server-db < "$1"; }

case "${1:?usage: upgrade.sh server|wallets}" in
server)
	. ./fallback/signet-fallback.env
	for image in captaind bark; do sudo docker image inspect "abandon-ship/$image:fallback-$FORK_COMMIT" >/dev/null; done
	dcf stop fb-web fb-api fb-barkd fb-watchmand fb-captaind
	# Idempotent: CREATE/ADD ... IF NOT EXISTS only.
	psql_file fallback/sql/expiry-settlement.sql
	psql_file fallback/sql/expiry-fallback.sql
	./fallback/render.sh enabled >/dev/null
	dcf up -d --no-deps fb-captaind
	wait_healthy fb-captaind
	dcf up -d --no-deps fb-watchmand ;;
wallets)
	dcf up -d --no-deps --force-recreate fb-ark
	dcf up -d --no-deps fb-barkd fb-api fb-web ;;
*) echo "unknown step $1" >&2; exit 1 ;;
esac
dcf ps
