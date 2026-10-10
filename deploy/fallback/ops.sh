#!/bin/bash
# Operator helpers for the expiry-fallback stack, on the VM, from deploy/.
#   ./fallback/ops.sh barkd GET wallet/fallback-destination   barkd REST, bearer token stays here
#   ./fallback/ops.sh barkd POST onchain/addresses/next '{}'
#   BARKD=fb-barkd2 ./fallback/ops.sh barkd GET wallet/balance    the second web wallet
#   ./fallback/ops.sh sql "select ..."                          new stack's database
#   ./fallback/ops.sh cli <bitcoin-cli args>                    shared signet bitcoind
#   ./fallback/ops.sh capt <captaind args>                      e.g. rpc wallet
#   ./fallback/ops.sh logs <service> [since]                    e.g. logs fb-captaind 1h
# Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
SIGNET_DEPLOY=${SIGNET_DEPLOY:-$HOME/abandon-ship-server/deploy}
p=abandon-signet-fallback
cmd=${1:?usage: see header}; shift
case "$cmd" in
barkd)
	method=$1 path=$2 body=${3:-}
	c=$p-${BARKD:-fb-barkd}-1
	token=$(sudo docker exec $c cat /data/.bark/auth_token)
	ip=$(sudo docker inspect -f "{{(index .NetworkSettings.Networks \"${p}_default\").IPAddress}}" $c)
	args=(-sS --fail-with-body -X "$method" -H "Authorization: Bearer $token" -H 'content-type: application/json')
	[ -n "$body" ] && args+=(-d "$body")
	curl "${args[@]}" "http://$ip:4000/api/v1/$path"; echo ;;
sql)
	sudo ./fallback/dcf exec -T fb-postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d bark-server-db -Atc "$1" ;;
cli)
	. "$SIGNET_DEPLOY/state/signet/secrets.env"
	cd "$SIGNET_DEPLOY" && sudo ./dc signet exec -T bitcoind bitcoin-cli -signet -rpcport=8332 -rpcuser="$RPC_USER" -rpcpassword="$RPC_PASS" "$@" ;;
capt)
	sudo ./fallback/dcf exec -T fb-captaind captaind --config /config/captaind.toml "$@" ;;
logs)
	sudo ./fallback/dcf logs --no-color --since "${2:-1h}" "$1" ;;
*) echo "unknown command $cmd" >&2; exit 1 ;;
esac
