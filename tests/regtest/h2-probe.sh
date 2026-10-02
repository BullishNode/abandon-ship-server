#!/bin/bash
# H2/O20 with the sidecar's real flow: the owner's interactive refresh is
# waiting for a round; the sidecar's ban lands before the round starts, or
# 0.5 s / 2 s / 4.5 s after "Round started" (inside the submit window). With ban_wait_secs=45 the claim
# comes after the round is persisted, so captaind must not crash, and exactly
# one side wins each coin.
. "$(dirname "$0")/lib.sh"
mkcfg ban_wait_secs=45
cd "$R"
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CAP0=$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' abandon-regtest-captaind-1)
OFFSETS=(pre 0.5 2 4.5)
WS=(); XS=()
for i in 0 1 2 3; do
	WS+=("$(wname p$i)"); newwallet "${WS[$i]}"
	round 60000 "${WS[$i]}"; XS+=("$(coins "${WS[$i]}")")
	mine 50   # staggered expiries: one candidate per probe
done
rounds() { docker compose logs --since "$START" captaind 2>&1 | grep -c 'Round started'; }

for i in 0 1 2 3; do
	W=${WS[$i]}; X=${XS[$i]}; O=${OFFSETS[$i]}
	expire_and_sweep "$X" || finish
	"$R/bark" "$W" refresh --vtxo "$X" > "$LOG/refresh-$i.log" 2>&1 & P=$!
	until grep -q 'Waiting for a round start' "$LOG/refresh-$i.log"; do sleep 0.2; done
	if [ "$O" != pre ]; then
		N=$(rounds); until [ "$(rounds)" -gt "$N" ]; do sleep 0.1; done
		sleep "$O"
	fi
	tick
	check "probe $O: banned, or refreshed before the tick" test -n \
		"$(q "SELECT 1 FROM sidecar.ban WHERE vtxo_id='$X'")$(q "SELECT 1 FROM vtxo WHERE vtxo_id='$X' AND spent_in_round IS NOT NULL")"
	wait $P
	for t in 1 2 3; do
		[ -n "$(payout_state "$X")$(q "SELECT spent_in_round FROM vtxo WHERE vtxo_id='$X' AND spent_in_round IS NOT NULL")" ] && break
		sleep $(( $(ban_wait) + 1 )); tick
	done
	REFRESHED=$(q "SELECT 1 FROM vtxo WHERE vtxo_id='$X' AND spent_in_round IS NOT NULL")
	PAID=$(payout_state "$X")
	say "probe $O: refreshed='${REFRESHED}' payout='${PAID}'"
	check "probe $O: exactly one winner" test -n "$REFRESHED$PAID" -a -z "$( [ -n "$REFRESHED" ] && [ -n "$PAID" ] && echo both)"
done
check "captaind never restarted" eq "$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}}' abandon-regtest-captaind-1)" "$CAP0"
check "no fatal round error" eq "$(docker compose logs --since "$START" captaind 2>&1 | grep -cE 'Fatal round error|critical worker stopped')" 0
confirm_payouts
finish
