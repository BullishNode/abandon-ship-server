#!/bin/bash
# A backup can precede repair of a failed round that referenced a later-paid
# coin. Model those restored rows with captaind stopped; startup must refuse
# to replay over them. Also refuse a failed one-shot recovery when Core is
# unreachable. This is a row fixture, not an interrupted live round.
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
check "fixture paid" pay_until "$X"
TX=$(payout_txid "$X")
[ -n "$TX" ] || finish
confirm_payouts
RAW=$(journal_raw "$X")
TRA=$(tr_address "$PK")
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/current.dump" || exit 2
STOPPED=0
(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
STOPPED=1
restore_current() {
	[ "$STOPPED" = 1 ] || return 0
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/current.dump" > "$LOG/cleanup.log" 2>&1 || return 1
	(cd "$R" && docker compose start captaind watchmand > /dev/null) || return 1
	STOPPED=0
	healthy captaind
}
trap 'restore_current || echo "SETUP FAIL: restore current.dump before restarting captaind" >&2' EXIT
q "BEGIN;
	DELETE FROM sidecar.payout WHERE vtxo_id='$X';
	UPDATE vtxo SET spend_state='spendable', updated_at=NOW() WHERE vtxo_id='$X';
	WITH p AS (INSERT INTO round_participation (created_at,round_id)
		VALUES (NOW(),'restore-pending-$RUN') RETURNING id)
	INSERT INTO round_part_input SELECT id,'$X' FROM p;
	COMMIT" > /dev/null || exit 2
eq "$(payout_state "$X")|$(spend_state "$X")" '|spendable' || exit 2
eq "$(q "SELECT count(*) FROM round_part_input i JOIN round_participation p ON p.id=i.participation_id WHERE i.vtxo_id='$X' AND p.forfeited_at IS NULL")" 1 || exit 2
RC=0; tick || RC=$?
check "startup refuses unfinished restored round" test "$RC" -ne 0
check "operator gets offline round recovery action" grep -q 'repair the round offline before restarting captaind' "$LOG/tick.log"
check "refusal precedes rewriting the restored coin" eq "$(spend_state "$X")" spendable
check "original payout bytes survive" eq "$(journal_raw "$X")" "$RAW"
check "no extra payment during refused recovery" eq "$(wallet_sends_to "$TRA")" 1

# Resolve the modeled old participation, but leave Core unavailable to the
# recovery command. Exit zero must mean that its recovery tick completed.
q "BEGIN;
	DELETE FROM round_part_input WHERE vtxo_id='$X' AND participation_id IN
		(SELECT id FROM round_participation WHERE round_id='restore-pending-$RUN');
	DELETE FROM round_participation WHERE round_id='restore-pending-$RUN';
	UPDATE vtxo SET spend_state='spendable', updated_at=NOW() WHERE vtxo_id='$X';
	COMMIT" > /dev/null || exit 2
eq "$(spend_state "$X")" spendable || exit 2
cp "$CFG" "$LOG/reachable.toml"
python3 - "$CFG" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
text, count = re.subn(r'^url = .*$', 'url = "http://127.0.0.1:1/wallet/payout"', p.read_text(), flags=re.M)
assert count == 1
p.write_text(text)
PY
RC=0; tick || RC=$?
check "failed one-shot recovery does not report success" test "$RC" -ne 0
check "unreconciled restored coin remains visible" eq "$(spend_state "$X")" spendable
cp "$LOG/reachable.toml" "$CFG"
RC=0; tick || RC=$?
check "recovery succeeds after Core returns" eq "$RC" 0
check "recovered coin is excluded before captaind restarts" eq "$(spend_state "$X")" spent
check "recovery still preserves the original bytes" eq "$(journal_raw "$X")" "$RAW"
check "successful recovery adds no second payment" eq "$(wallet_sends_to "$TRA")" 1
restore_current || exit 2
finish
