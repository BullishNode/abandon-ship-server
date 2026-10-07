#!/bin/sh
set -eu
if [ ! -f /data/captaind/mnemonic ]; then
  captaind --config /config/captaind.toml create
fi
exec captaind --config /config/captaind.toml start
