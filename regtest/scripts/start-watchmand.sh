#!/bin/sh
set -eu
umask 077
mkdir -p /data/watchmand
if [ ! -f /data/watchmand/mnemonic ]; then
  cp /data/captaind/mnemonic /data/watchmand/mnemonic
fi
exec watchmand --config /config/watchmand.toml start
