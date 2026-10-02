#!/bin/bash
# Fees rise after a small coin S was claimed: S is no longer affordable and
# stays claimed. A big coin B that expires meanwhile must still be claimed and
# paid; S is paid once fees fall. The fee rise is simulated by lowering
# max_fee_pct_per_payout (same affordability test).
. "$(dirname "$0")/lib.sh"
S=$(wname s); B=$(wname b)
newwallet "$S"; newwallet "$B"
round 20000 "$S"; round 500000 "$B"
XS=$(coins "$S"); XB=$(coins "$B")
read -r PKS AMTS <<< "$(coininfo "$S" "$XS")"; read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XS $XB" || finish

# Phase 1: only S is past grace; the payout wallet is locked, so S stays claimed.
EB=$(q "SELECT expiry FROM vtxo WHERE vtxo_id='$XB'")
mkcfg grace_blocks=$(( $(tip) - EB + 1 )) max_fee_pct_per_payout=20
lock_wallet
check "S claimed" pay_until "$XS" 4 "'claimed'"
check "B untouched" eq "$(bans "$XB")" 0

# Phase 2: fees "rise": S unaffordable, B affordable.
mkcfg max_fee_pct_per_payout=1
unlock_wallet
check "B paid while S waits" pay_until "$XB" 4
check "S still claimed" eq "$(payout_state "$XS")" claimed

# Phase 3: fees "fall".
mkcfg max_fee_pct_per_payout=20
check "S paid" pay_until "$XS" 3
assert_paid "$XS" "$PKS" "$AMTS"
assert_paid "$XB" "$PKB" "$AMTB"
confirm_payouts
finish
