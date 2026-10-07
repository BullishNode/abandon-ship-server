#!/bin/sh
# Render state/<stack>/*.toml from templates/ and networks/<stack>.env.
# Creates state/<stack>/secrets.env on first run. SWEEP_ADDRESS (the payout
# wallet address that watchmand sweeps into) goes in state/<stack>/sweep.env.
set -eu
cd "$(dirname "$0")"
stack=${1:?usage: ./render.sh <signet|mainnet>}
[ -f "networks/$stack.env" ] || { echo "no networks/$stack.env" >&2; exit 1; }
umask 077
mkdir -p "state/$stack/journal"
if [ ! -f "state/$stack/secrets.env" ]; then
	printf 'RPC_USER=ark\nRPC_PASS=%s\nPG_PASS=%s\n' "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > "state/$stack/secrets.env"
fi
set -a
. "./networks/$stack.env"
. "./state/$stack/secrets.env"
[ -f "state/$stack/sweep.env" ] && . "./state/$stack/sweep.env"
set +a
render() { envsubst < "templates/$1.toml.tmpl" > "state/$stack/$1.toml"; }
render captaind
if [ -n "${SWEEP_ADDRESS:-}" ]; then
	render watchmand
	render sidecar
else
	echo "SWEEP_ADDRESS unset: rendered captaind.toml only" >&2
fi
ls -l "state/$stack"
