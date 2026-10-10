#!/bin/sh
# Render state/signet-fallback/ from fallback/templates.
# ./fallback/render.sh [enabled]   (enabled: turn on [expiry_payout]; needs sweep.env)
# Creates its own PG_PASS on first run. Bitcoind RPC credentials come from the
# signet stack's state/signet/secrets.env (shared node); nothing is printed.
set -eu
cd "$(dirname "$0")/.."
SIGNET_DEPLOY=${SIGNET_DEPLOY:-$HOME/abandon-ship-server/deploy}
umask 077
dir=state/signet-fallback
mkdir -p "$dir"
if [ ! -f "$dir/secrets.env" ]; then
	printf 'PG_PASS=%s\n' "$(openssl rand -hex 24)" > "$dir/secrets.env"
fi
set -a
. ./fallback/signet-fallback.env
. "$SIGNET_DEPLOY/state/signet/secrets.env"
. "./$dir/secrets.env"
[ -f "$dir/sweep.env" ] && . "./$dir/sweep.env"
set +a
EXPIRY_ENABLED=false
if [ "${1:-}" = enabled ]; then
	[ -n "${SWEEP_ADDRESS:-}" ] || { echo "SWEEP_ADDRESS unset" >&2; exit 1; }
	EXPIRY_ENABLED=true
fi
export EXPIRY_ENABLED
envsubst < fallback/templates/captaind.toml.tmpl > "$dir/captaind.toml"
if [ -n "${SWEEP_ADDRESS:-}" ]; then
	envsubst < fallback/templates/watchmand.toml.tmpl > "$dir/watchmand.toml"
else
	echo "SWEEP_ADDRESS unset: rendered captaind.toml only" >&2
fi
# barkd reads this as its bitcoind cookie (user:pass).
printf '%s:%s' "$RPC_USER" "$RPC_PASS" > "$dir/core.cookie"
ls -l "$dir"
