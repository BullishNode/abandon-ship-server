#!/bin/bash
# The owner's interactive refresh is
# waiting for a round; the sidecar's ban lands before the round starts, or
# 0.5 s / 2 s / 4.5 s after "Round started" (inside the submit window). With
# ban_wait_secs=45 the claim comes after the round is persisted, so captaind
# must not crash, and exactly one side wins each coin.
. "$(dirname "$0")/lib.sh"
mkcfg ban_wait_secs=45
cd "$R"
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CAP0=$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' "$PROJECT-captaind-1")
OFFSETS=(pre 0.5 2 4.5)
rounds() { docker compose logs --since "$START" captaind 2>&1 | grep -c 'Round started'; }

for i in 0 1 2 3; do
	W=$(wname p$i); O=${OFFSETS[$i]}
	newwallet "$W"; round 60000 "$W"; X=$(coins "$W")
	expire_and_sweep "$X" || finish
	N=$(rounds)
	timeout 120 "$R/bark" "$W" refresh --vtxo "$X" > "$LOG/refresh-$i.log" 2>&1 & P=$!
	until grep -q 'Waiting for a round start' "$LOG/refresh-$i.log"; do
		kill -0 "$P" 2>/dev/null || { check "probe $O reached the round wait" false; finish; }
		sleep 0.2
	done
	if [ "$O" != pre ]; then
		until [ "$(rounds)" -gt "$N" ]; do
			kill -0 "$P" 2>/dev/null || { check "probe $O reached a round" false; finish; }
			sleep 0.1
		done
		sleep "$O"
	fi
	tick
	check "probe $O: banned, or refreshed before the tick" test -n \
		"$(q "SELECT 1 FROM sidecar.ban WHERE vtxo_id='$X'")$(refreshed "$X")"
	wait $P
	for t in 1 2 3; do
		[ -n "$(payout_state "$X")$(refreshed "$X")" ] && break
		sleep $(( $(ban_wait) + 1 )); tick
	done
	REFRESHED=$(refreshed "$X")
	PAID=$(payout_state "$X")
	say "probe $O: refreshed='${REFRESHED}' payout='${PAID}'"
	check "probe $O: exactly one winner" test -n "$REFRESHED$PAID" -a -z "$( [ -n "$REFRESHED" ] && [ -n "$PAID" ] && echo both)"
done
check "captaind never restarted" eq "$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' "$PROJECT-captaind-1")" "$CAP0"
check "no fatal round error" eq "$(docker compose logs --since "$START" captaind 2>&1 | grep -cE 'Fatal round error|critical worker stopped')" 0
confirm_payouts
finish
