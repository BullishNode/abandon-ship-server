#!/bin/bash
# Each gate before the claim holds on its own, re-checked every tick:
# - the sweep is shallower than sweep_min_confs: no ban;
# - the coin is in an open round participation: no ban; and again after the
#   ban wait: no claim.
# Then paid once. The participation row is a stand-in for the one captaind
# writes when a user submits the coin to a round (as race-held-lock's spend).
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
SPENDER=$(anchor_spender "$(anchor_of "$X")")
DEPTH=$(btc getrawtransaction "$SPENDER" 1 | python3 -c "import json,sys;print(json.load(sys.stdin)['confirmations'])")
mkcfg sweep_min_confs=$((DEPTH + 3))
tick
check "sweep $DEPTH deep < $((DEPTH + 3)): no ban" eq "$(bans "$X")" 0
mine 3

join() { q "WITH p AS (INSERT INTO round_participation (created_at, round_id) VALUES (now(), 'claim-gates-$RUN') RETURNING id)
	INSERT INTO round_part_input SELECT id, '$X' FROM p" > /dev/null; }
leave() { q "WITH d AS (DELETE FROM round_part_input WHERE vtxo_id='$X' AND participation_id IN
	(SELECT id FROM round_participation WHERE round_id='claim-gates-$RUN') RETURNING participation_id)
	DELETE FROM round_participation WHERE id IN (SELECT participation_id FROM d)" > /dev/null; }
trap leave EXIT
join; tick; leave
check "deep enough, in a round: no ban" eq "$(bans "$X")" 0
tick
check "deep enough, out of the round: banned" eq "$(bans "$X")" 1
sleep $(( $(ban_wait) + 1 ))
join; tick; leave
check "ban wait over, in a round: no claim" eq "$(payout_state "$X")" ""
check "out of the round: paid" pay_until "$X" 2
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
