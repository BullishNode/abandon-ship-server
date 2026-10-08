#!/bin/bash
# A shared-key payout publishes its exact output deduction. Publication failure
# must not prevent payment; regeneration must not mutate the payment ledger.
. "$(dirname "$0")/lib.sh"
mkcfg
S=$(wname sender); T=$(wname receiver)
newwallet "$S"; newwallet "$T" 0
round 200000 "$S"
ADDR=$(arkaddr "$T")
bark "$S" send "$ADDR" '40000 sat' > "$LOG/send1.log" || exit 2
bark "$S" send "$ADDR" '30000 sat' > "$LOG/send2.log" || exit 2
mapfile -t IDS < <(coins "$T")
check "two coins to a reused key" eq "${#IDS[@]}" 2
[ ${#FAILS[@]} -eq 0 ] || finish
expire_and_sweep "${IDS[*]}" || finish

RECEIPTS=${JOURNAL%.*}.receipts
restore_receipts() {
	[ ! -f "$RECEIPTS" ] || rm -- "$RECEIPTS"
	[ ! -d "$LOG/saved-receipts" ] || mv "$LOG/saved-receipts" "$RECEIPTS"
}
[ ! -e "$RECEIPTS" ] || mv "$RECEIPTS" "$LOG/saved-receipts"
trap restore_receipts EXIT
touch "$RECEIPTS"
check "payment succeeds while receipt directory is blocked" pay_until "${IDS[*]}" 3
TXID=$(payout_txid "${IDS[0]}")
check "shared-key coins paid in the same transaction" eq "$(payout_txid "${IDS[1]}")" "$TXID"
check "receipt publication failure is visible" grep -q 'fee receipt unavailable' "$LOG/sidecar.log"
restore_receipts; trap - EXIT
tick || exit 2
check "retry creates the receipt" test -f "$RECEIPTS/$TXID.json"
[ ${#FAILS[@]} -eq 0 ] || finish

# Coin amounts and scripts come from stored VTXO bytes, not payout assertions.
# The common oracle independently obtains actual transactions and input values.
q "SELECT v.vtxo_id || '|' || encode(v.vtxo,'hex') FROM vtxo v
   JOIN sidecar.payout p USING (vtxo_id) WHERE p.txid='$TXID'" > "$LOG/coins.txt"
python3 - "$ROOT" "$LOG" "$TXID" "$RECEIPTS" <<'PY'
import json, subprocess, sys
from pathlib import Path
root, log, txid, receipts = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
sys.path.insert(0, str(root/'tests/comparison'))
from oracle import capture, verify, sat
coins = {}
for row in (log/'coins.txt').read_text().splitlines():
    coin, raw = row.split('|')
    facts = json.loads(subprocess.check_output(['cargo', 'run', '-q', '--locked', '--example', 'vtxo_facts'], input=raw, text=True, cwd=root))
    assert facts.pop('id') == coin
    coins[coin] = facts
evidence = capture(dict(coins=coins, payouts=[dict(txid=txid, coins=list(coins))], max_fee_pct=20), str(root/'regtest/btc'))
(log/'chain-evidence.json').write_text(json.dumps(evidence, indent=2, default=str)+'\n')
print(json.dumps(verify(evidence)))
gross = {}
for coin in coins.values():
    script = coin['payout_script']
    gross[script] = gross.get(script, 0) + coin['amount_sat']
expected = []
for output in evidence['transactions'][txid]['vout']:
    script = output['scriptPubKey']['hex']
    if script in gross:
        net = sat(output['value'])
        expected.append(dict(vout=output['n'], amount_sat=net, fee_sat=gross[script]-net))
receipt = json.loads((receipts/(txid+'.json')).read_text())
assert receipt == dict(txid=txid, outputs=sorted(expected, key=lambda o:o['vout']))
assert len(expected) < len(coins), 'fixture did not combine shared-key coins'
(log/'receipt.json').write_text(json.dumps(receipt)+'\n')
PY
check "receipt equals actual shared-key deduction and excludes change" eq "$?" 0

cp "$RECEIPTS/$TXID.json" "$LOG/original.json"
rm -- "$RECEIPTS/$TXID.json"
BEFORE_JOURNAL=$(sha256sum "$JOURNAL")
ledger_hash() { q "SELECT md5(coalesce(string_agg(row_to_json(p)::text, ',' ORDER BY vtxo_id),'')) FROM sidecar.payout p"; }
BEFORE_LEDGER=$(ledger_hash)
"$BIN" "$CFG" --export-receipts > "$LOG/export.log" 2>&1
say "receipt export exit=$?; historical rows missing after earlier restore tests can prevent a complete export"
check "export reconstructs identical selected receipt" cmp "$LOG/original.json" "$RECEIPTS/$TXID.json"
check "export leaves journal bytes unchanged" eq "$(sha256sum "$JOURNAL")" "$BEFORE_JOURNAL"
check "export leaves payment rows unchanged" eq "$(ledger_hash)" "$BEFORE_LEDGER"
# Model a restore that loses payout rows while keeping Ark history and the
# independent journal. This is an explicit row fixture, not a WAL restore.
q "COPY (SELECT * FROM sidecar.payout WHERE txid='$TXID' ORDER BY vtxo_id) TO STDOUT WITH CSV" > "$LOG/payout-rows.csv" || exit 2
test -s "$LOG/payout-rows.csv" || exit 2
restore_rows() {
	"$R/psql" -q -c 'COPY sidecar.payout FROM STDIN WITH CSV' < "$LOG/payout-rows.csv" > /dev/null || return 1
}
q "DELETE FROM sidecar.payout WHERE txid='$TXID'" > /dev/null || exit 2
trap restore_rows EXIT
rm -- "$RECEIPTS/$TXID.json"
LOST_LEDGER=$(ledger_hash)
"$BIN" "$CFG" --export-receipts > "$LOG/export-journal-only.log" 2>&1
say "journal-only export exit=$?"
check "journal-only receipt retains exact original fee" cmp "$LOG/original.json" "$RECEIPTS/$TXID.json"
check "journal-only export leaves payment rows unchanged" eq "$(ledger_hash)" "$LOST_LEDGER"
check "journal-only export leaves journal unchanged" eq "$(sha256sum "$JOURNAL")" "$BEFORE_JOURNAL"
restore_rows || exit 2
trap - EXIT
check "fixture restored original rows" eq "$(ledger_hash)" "$BEFORE_LEDGER"
confirm_payouts
finish
