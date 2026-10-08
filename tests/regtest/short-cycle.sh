#!/bin/bash
# Test-only short lifetime with matching wallet exit margin. This happy path
# measures the minimum cycle; deep exit trees need their own timing tests.
. "$(dirname "$0")/lib.sh"
LIFETIME=${1:-4}
MARGIN=${2:-1}
GRACE=0
SWEEP_CONFS=1
cp "$R/captaind.toml" "$LOG/captaind-original.toml"
cleanup() {
	cp "$LOG/captaind-original.toml" "$R/captaind.toml"
	(cd "$R" && docker compose restart captaind > /dev/null 2>&1)
}
trap cleanup EXIT
python3 - "$R/captaind.toml" "$LIFETIME" <<'CONFIG' || exit 2
from pathlib import Path
import re,sys
p=Path(sys.argv[1]); s=p.read_text()
for name,value in [('vtxo_lifetime',int(sys.argv[2])),('vtxo_exit_delta',1),('required_board_confirmations',1)]:
    s,n=re.subn(r'^'+name+r'\s*=.*$',name+' = '+str(value),s,flags=re.M)
    assert n==(2 if name=="vtxo_lifetime" else 1),name
p.write_text(s)
CONFIG
(cd "$R" && docker compose restart captaind > /dev/null 2>&1) || exit 2
healthy captaind || exit 2
W=$(wname owner)
newwallet "$W"
# Only this scenario's fresh wallet; no existing wallet settings are changed.
(cd "$R" && docker compose --profile cli run --rm -T --entrypoint sh bark -c '
    file="/wallets/$1/config.toml"
    { printf "vtxo_exit_margin = %s\nvtxo_refresh_expiry_threshold = 1\n" "$2"; sed -E "/^vtxo_(exit_margin|refresh_expiry_threshold)[[:space:]]*=/d" "$file"; } > "$file.tmp"
    chmod 600 "$file.tmp"
    mv "$file.tmp" "$file"
' _ "$W" "$MARGIN") || exit 2
bark "$W" config > "$LOG/wallet-config.json" || exit 2
check "test wallet uses requested exit margin" python3 - "$LOG/wallet-config.json" "$MARGIN" <<'CONFIG'
import json,sys
assert json.load(open(sys.argv[1]))['vtxo_exit_margin']==int(sys.argv[2])
CONFIG
[ ${#FAILS[@]} -eq 0 ] || finish
ensure_round_funding || exit 2
mine 1
START=$(tip)
bark "$W" board '20000 sat' > "$LOG/board.json" || exit 2
mine 1
bark "$W" balance > "$LOG/board-balance.json" || exit 2
OLD=$(coins "$W")
check "board is still unexpired before refresh" test "$(q "SELECT min(expiry) FROM vtxo WHERE vtxo_id='$OLD'")" -gt "$(tip)"
bark "$W" refresh --all > "$LOG/refresh.json" || exit 2
mine 1
X=$(coins "$W")
check "one refreshed coin before expiry" eq "$(wc -w <<< "$X")" 1
[ ${#FAILS[@]} -eq 0 ] || finish
read -r PK AMT <<< "$(coininfo "$W" "$X")"
# The actual expiry is in the VTXO; preserve it with the observed heights.
printf 'lifetime=%s\nexit_margin=%s\nstart_height=%s\nround_observed_height=%s\ncoin=%s\nexpiry=%s\n' "$LIFETIME" "$MARGIN" "$START" "$(tip)" "$X" "$(q "SELECT expiry FROM vtxo WHERE vtxo_id='$X'")" > "$LOG/cycle.txt"
expire_and_sweep "$X" || finish
mkcfg grace_blocks=0 sweep_min_confs=1
check "short-cycle payout broadcasts" pay_until "$X" 4
assert_paid "$X" "$PK" "$AMT"
printf 'payout_height=%s\npayout_txid=%s\n' "$(tip)" "$(payout_txid "$X")" >> "$LOG/cycle.txt"
confirm_payouts
finish
