#!/bin/sh
# Start the RPC-adapter deployment with a fresh native replay input every time.
# Stop captaind, watchmand and the sidecar writer before running this command.
set -eu
if [ "$#" -ne 3 ]; then
	printf 'Usage: %s SIDECAR_CONFIG CAPTAIND_CONFIG REPLAY_IDS\n' "$0" >&2
	exit 2
fi
"${SIDECAR_BIN:-abandon-ship-server}" "$1" --export-settlement-ids "$3"
# Override a stale/omitted path in the captaind config with the export just made.
export BARK_SERVER__SETTLEMENT_REPLAY_IDS="$3"
exec "${CAPTAIND_BIN:-captaind}" -C "$2" start
