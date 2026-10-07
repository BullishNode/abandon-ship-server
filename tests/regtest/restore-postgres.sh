#!/bin/bash
# Run the journal retry scenario against an actual pre-claim pg_dump/pg_restore.
RESTORE_MODE=postgres
. "$(dirname "$0")/restore-retry.sh"
