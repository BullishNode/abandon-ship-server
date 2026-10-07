#!/bin/sh
set -eu
if [ ! -f /data/captaind/mnemonic ]; then
  captaind --config /config/captaind.toml create
fi
/bin/sh /scripts/wait-reassert.sh
exec captaind --config /config/captaind.toml start
