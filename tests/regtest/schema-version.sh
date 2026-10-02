#!/bin/bash
# #26/T10: an unknown captaind schema version stops the sidecar: at startup,
# and under a running loop (captaind upgraded underneath it).
. "$(dirname "$0")/lib.sh"
mkcfg 'allowed_schema_versions=[66]'
tick; RC=$?
check "startup refused (exit $RC)" test $RC -ne 0
check "reason" grep -q "schema version 67 not in allowed_schema_versions" "$LOG/tick.log"

mkcfg poll_interval_secs=2
start_loop
check "loop running on 67" eq "$(loop_exit 1)" running
q "INSERT INTO refinery_schema_history (version, name, applied_on, checksum) VALUES (68, 'suite_fake_upgrade', 'now', '0')" > /dev/null
RC=$(loop_exit 30)
q "DELETE FROM refinery_schema_history WHERE version = 68 AND name = 'suite_fake_upgrade'" > /dev/null
check "loop stops on 68 (exit $RC)" test "$RC" != running -a "$RC" != 0
[ "$RC" = running ] && stop_loop
check "reason" grep -q "captaind schema version changed to 68" "$LOG/loop.log"
check "fake version removed" eq "$(q "SELECT max(version) FROM refinery_schema_history")" 67
finish
