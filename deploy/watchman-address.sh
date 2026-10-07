#!/bin/sh
# Print watchmand's wallet addresses: tr(master/38644432h/0/*) from captaind's
# mnemonic (bark server/src/wallet/mod.rs). The key stays on this host.
# ./watchman-address.sh <signet|mainnet> [count]
set -eu
cd "$(dirname "$0")"
stack=${1:?usage: ./watchman-address.sh <signet|mainnet> [count]}; n=${2:-1}
set -a; . "./networks/$stack.env"; . "./state/$stack/secrets.env"; set +a
[ "$NETWORK" = bitcoin ] && ver=0488ade4 || ver=04358394
MN=$(./dc "$stack" exec -T captaind cat /data/captaind/mnemonic)
export MN VER=$ver
xprv=$(python3 - <<'PY'
import hashlib, hmac, os
seed = hashlib.pbkdf2_hmac("sha512", os.environ["MN"].strip().encode(), b"mnemonic", 2048)
I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
data = bytes.fromhex(os.environ["VER"]) + b"\x00" * 9 + I[32:] + b"\x00" + I[:32]
data += hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
v, s = int.from_bytes(data, "big"), ""
while v:
	v, r = divmod(v, 58)
	s = A[r] + s
print(s)
PY
)
cli() { ./dc "$stack" exec -T bitcoind bitcoin-cli "$BITCOIND_NET" -rpcport=8332 -rpcuser="$RPC_USER" -rpcpassword="$RPC_PASS" "$@"; }
unset MN
d="tr($xprv/38644432h/0/*)"
cs=$(cli getdescriptorinfo "$d" | python3 -c 'import json,sys; print(json.load(sys.stdin)["checksum"])')
cli deriveaddresses "$d#$cs" "[0,$((n - 1))]"
