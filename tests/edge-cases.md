# Edge cases (50, in five framings)

Status: `todo` · `pass` · `FAIL` (bug found) · `fixed` · `n/a` (not testable on this stack, reasoned instead).

## Batch 1: "How could the sidecar pay twice?" (races and state)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 1 | Delegated refresh stored before the ban; its round finalises after the claim | Exactly one wins; the loser fails cleanly | pass (delegated refresh spent it in a round; never claimed) |
| 2 | Interactive refresh mid-round (submit phase) when the claim runs | The ban + wait prevents a halted round (H2); one winner | pass after fix: H2 crashes captaind if a claim lands in the submit window (O20); banned coins cannot be submitted, and the claim now needs our ban intact (suite: `h2-probe`) |
| 3 | Scheduled delegated refresh registered pre-expiry with a height past expiry | Server rejects it at submit; it does not block payout forever | pass (scheduling past expiry rejected at submit) |
| 4 | Offboard session opened before the ban, finished after the claim | One winner | n/a: a ban blocks offboard start; open sessions end within `offboard_session_timeout` < wait |
| 5 | Lightning send phase 1 on an expired coin, concurrent with the claim | One winner | n/a: no LN channel on this stack; LN phase 1 commit is conditional (code) |
| 6 | Two sidecar instances started together | Second exits on the leader lock | pass (suite: `infra-postgres`) |
| 7 | Crash after the claim commit, before building the tx | Restart pays once | pass (suite: `crash-signed`) |
| 8 | Crash after `mark_signed`, before broadcast | Restart broadcasts the same tx | pass (suite: `crash-signed`) |
| 9 | A built tx is never stored (`mark_signed` fails) | That tx is never broadcast; the wallet does not double-spend later | pass (code: `mark_signed` guard; only stored txs are broadcast) |
| 10 | Payout tx dropped from the mempool | No second, different payout for the same coin | fixed: `broadcast` rows with 0 confs are rebroadcast (same tx); bitcoind wallet also rebroadcasts on load |

## Batch 2: "I am the attacker" (malicious user; DB-writer rows are out of scope)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 11 | User starts a unilateral exit before expiry; leaf confirms | Never paid | pass (suite: `exit-full`) |
| 12 | One user unrolls part of the tree; every other coin in the round is then a "non-sweep" | The round is quarantined: no double pay, but a payout DoS on the others | pass (quarantined; payout DoS for the round, O6) (suite: `exit-full`) |
| 13 | User splits into many dust coins | Below `min_payout_sat`: not paid; no fee drain | pass (suite: `fee-pct-rule`) |
| 14 | Coin at max arkoor depth | Validates and pays like any other | todo |
| 15 | User refreshes right after losing the claim race | Refused by captaind | pass (suite: `happy-single`) |
| 16 | User restores from seed after the payout | Coin not resurrected | pass (suite: `unclaimed-delegated: seed sweep`) |
| 17 | DB writer changes `vtxo.amount` | Amount mismatch → quarantine, no pay | n/a: DB writers are out of scope (trusted DB) |
| 18 | DB writer inserts a forged `vtxo` row/blob | Validation fails → quarantine | n/a: DB writers are out of scope (trusted DB) |
| 19 | DB writer sets `onchain_spent_txid` to an unrelated tx | "does not spend the anchor" → quarantine | n/a: DB writers are out of scope (trusted DB) |
| 20 | DB writer edits the expiry column into the past | The VTXO's own expiry decides → wait | n/a: DB writers are out of scope (trusted DB) |

## Batch 3: "Ops is having a bad day" (infrastructure)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 21 | Postgres down mid-tick | Tick errors, retried; no half state | pass (exits; supervisor restarts) (suite: `infra-postgres`) |
| 22 | bitcoind down | Tick errors, retried | pass (+O17 fix: wallet check every tick) (suite: `infra-bitcoind`) |
| 23 | Payout wallet empty | Claims wait as `claimed`; no crash loop | pass |
| 24 | Fee estimate spikes | Fee cap defers the batch | pass |
| 25 | Watchman down for a long time | No sweep, so nothing paid | pass (unswept → wait, as #31) |
| 26 | captaind upgraded (schema version changes) | Sidecar refuses to start | pass (suite: `schema-version`) |
| 27 | Wrong `network` in config | Refuses (address network mismatch) | pass |
| 28 | Wrong `sweep_addresses` in config | Must NOT mass-quarantine coins permanently | FAIL→fixed (wait + warn, no mass quarantine) |
| 29 | DB role missing a grant | Claim fails atomically, no partial state | pass (atomic; nothing partial) |
| 30 | Operator flips a paid coin back to spendable by hand | Invariant I2 stops the sidecar | pass (I2 stops the process) |

## Batch 4: "Bitcoin is weird" (chain level)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 31 | Reorg removes the sweep before `sweep_min_confs` | Wait; no claim | pass |
| 32 | Reorg removes a confirmed payout tx | Same tx re-mined or rebroadcast; no duplicate | pass |
| 33 | Payout tx stuck at low fee | Known gap: no RBF yet | gap: no RBF bump yet |
| 34 | Fee subtraction pushes an output below dust | Batch must not get stuck forever | near miss (400 sat → 333 out) → fixed with a config floor; see max-fee proposal |
| 35 | Batch hits standardness size limits | `max_batch` keeps it valid | reasoned (`max_batch`) |
| 36 | **Several coins share one pubkey** (same Ark address paid twice) | One output per address carrying the summed amount, or separate outputs; never a collapsed amount | FAIL→fixed (one output per address) (suite: `shared-address`) |
| 37 | Sweep tx spends outputs of several rounds | Still a sweep | pass |
| 38 | Anchor tx not found (no txindex / pruned) | Infra error, not quarantine | FAIL→fixed (wait, not tick abort) |
| 39 | Board coin (anchor = board tx) expires and is swept | Paid like a round coin | pass (board coin paid) |
| 40 | Sweep pays to the sweep address plus a P2A anchor | Still a sweep | pass (unit test) |

## Batch 5: "Outside the box" (time, humans, money, future)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 41 | User returns exactly at the expiry + G boundary | One winner | todo |
| 42 | Coin exactly at `min_payout_sat` | Paid (inclusive) | pass |
| 43 | Transient state (spender not indexed yet) is classified as permanent quarantine | Quarantine only for permanent faults | fixed (unavailable spender → wait; config → wait) |
| 44 | Lightning-received coin (server was the sender) expires | Paid to the user's key | todo |
| 45 | Expired HTLC coins (Lightning in flight) | Not paid by the sidecar; their own expiry rules apply | reasoned (`policy_type` filter) |
| 46 | Thousands of coins expire at once | Paid in successive batches; no tick blowup | reasoned (`max_batch` per tick) |
| 47 | The same user is paid in two batches to the same `tr(key)` | Address reuse: privacy note only | observed (u3 paid twice to the same tr(key)) |
| 48 | A payout lands on a key the user's wallet no longer scans (key index beyond its gap) | Documented recovery path | pass (range 0–200 found all) |
| 49 | Sweep and payout confirm in the same block as a user exit attempt | The exit is invalid after the sweep | todo |
| 50 | Bull disappears after payouts | Users can still spend `tr(coin key)` with the seed | pass (swept with a standard descriptor wallet) (suite: `unclaimed-delegated`) |

## Batch 6: "Races and timing" (requested follow-up)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 51 | TOCTOU: a participation is inserted after the last in-round check, before the claim | No double pay; risk is H2 (halted round), liveness only | confirmed real (H2) → mitigated, see #2 |
| 52 | captaind's spend transaction for the coin is open (uncommitted) when the claim runs | Claim blocks on the row lock, then returns Lost | pass (blocked ~13 s, then Lost) (suite: `race-held-lock`) |
| 53 | Claim transaction open (uncommitted) when captaind tries to spend the coin | captaind blocks, then its conditional update fails; nothing released | pass: rollback side wins (A11) (suite: `race-held-lock`) |
| 54 | Postgres connection drops; the advisory leader lock is lost; a second instance starts | Sidecar must exit on connection loss (no silent reconnect without the lock) | pass (exits on connection loss) (suite: `infra-postgres`) |
| 55 | Payout-wallet UTXO spent by an operator between build and broadcast | Stored tx invalid; must not rebuild until the old tx is provably dead | todo |
| 56 | User returns during the ban-wait window | Refresh refused, then paid on-chain: UX note | observed (refused, then paid on-chain; UX note) (suite: `race-user-refresh`) |
| 57 | Operator re-bans or unbans the coin via captaind admin while the sidecar waits | Ban shortened; safety unaffected (the claim is conditional) | FAIL→fixed (re-ban, wait restarts) (suite: `race-operator-unban`) |
| 58 | Exit leaf confirms between the sweep-depth check and the claim | Impossible after a wholesale sweep; the claim guard on `confirmed_height` holds anyway | reasoned (`confirmed_height` guard + sweep rule) |
| 59 | captaind restarts mid-round while the sidecar claims | In-memory locks lost; the DB guard is still atomic | todo |
| 60 | Payout batch timing and co-membership reveal which users were offline | Privacy note; observers can link users paid in one batch | note |

## Batch 7: "Exhaust it" (resource exhaustion, DoS)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 61 | Millions of dust coins expire at once; the candidate query scans them every tick | Bounded by `LIMIT max_batch` with an index; no OOM | FAIL→fixed: coins below `min_payout_sat` filled the `LIMIT` window and starved payable coins; now filtered in SQL before the limit |
| 62 | Dust coins fill every candidate slot forever (quarantine-or-wait loop starves real coins) | Quarantined dust leaves the window; real coins still progress | FAIL→fixed (as #61); coins above `min_payout_sat` but unaffordable at the current fee rate still occupy the window while fees are high |
| 63 | One huge VTXO blob (max exit depth) per candidate | Decode/validate memory bounded per coin | todo |
| 64 | Thousands of coins waiting on the same unavailable anchor tx: one RPC per coin per tick | RPC storm bounded by `max_batch`; consider a per-anchor cache | todo |
| 65 | `sidecar.ban` / `quarantine` grow without bound | Small rows; add retention for `ban` | todo |
| 66 | bitcoind RPC slow (seconds per call): tick takes minutes | Ticks never overlap (sequential loop) | todo |
| 67 | Postgres connection pool exhausted by captaind | Sidecar uses one connection; errors retried | todo |
| 68 | Payout batch near the 100 kvB standardness limit | `max_batch` × output size stays under the limit | todo |
| 69 | Mempool full: payout rejected (min relay fee rises) | Broadcast error retried; no rebuild | todo |
| 70 | Log flooding: a warn per coin per tick for stuck coins | Bounded by `max_batch`; acceptable | todo |

## Batch 8: "Kill it" (crashes, restarts, process lifecycle)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 71 | SIGKILL between ban and claim | Restart: ban age continues; claims later | todo |
| 72 | SIGKILL inside the claim transaction | Postgres rolls back; nothing half-done | todo |
| 73 | SIGKILL between `mark_signed` and broadcast | Restart rebroadcasts the stored tx | pass (suite: `crash-signed: stored tx journaled and broadcast on restart`) |
| 74 | OOM-kill during payout building | Same as 72/73 by stage | todo |
| 75 | Panic on unexpected data (unwrap) | No unwraps on external data; process exits non-zero, supervisor restarts | FAIL→fixed (O25: panic on a TEXT column; all reads are `try_get`) |
| 76 | Postgres restarts; the sidecar's connection is dead | Process must exit (and re-lock on restart), not spin forever | pass (as #21) (suite: `infra-postgres`) |
| 77 | bitcoind restarts mid-build | RPC error, tick retried; the wallet tx is not broadcast | pass (as #22) (suite: `infra-bitcoind`) |
| 78 | Disk full on the DB host | Writes fail atomically; tick retried | todo |
| 79 | Host clock jumps (NTP) | Ban age uses DB `NOW()`; heights drive everything else | todo |
| 80 | Sidecar binary built against a different `ark-lib` than captaind | Startup schema guard plus a decode/validate failure → quarantine, no pay | todo |

## Batch 9: "Feed it garbage" (malicious data, parsing)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 81 | `vtxo` blob truncated or random bytes | Quarantine "undecodable" | n/a: DB writers are out of scope (trusted DB) |
| 82 | Blob of a different coin (id mismatch) | Quarantine "blob id != row id" | n/a: DB writers are out of scope (trusted DB) |
| 83 | Blob with valid structure but a bad signature | Quarantine "failed validation" | n/a: DB writers are out of scope (trusted DB) |
| 84 | `anchor_point` text unparseable / `onchain_spent_txid` not hex | Per-coin error, not tick abort | n/a: DB writers are out of scope (trusted DB) |
| 85 | SQL injection via any DB string | All queries parameterised | n/a: DB writers are out of scope (trusted DB) |
| 86 | Payout address from a non-pubkey policy (checkpoint, HTLC) | Filtered by `policy_type`; validated policy | todo |
| 87 | Huge `amount` overflows i64 or u64 sums | Amount from a validated VTXO; sums checked | n/a: DB writers are out of scope (trusted DB) |
| 88 | Negative or zero amounts in the DB | Validated VTXO amount; DB mismatch → quarantine | n/a: DB writers are out of scope (trusted DB) |
| 89 | Sweep tx with zero non-anchor outputs (OP_RETURN only) | Not a sweep (`paid_any` false) | pass (unit test) |
| 90 | Config values out of range (`max_fee_share` 0, `grace` 0, `ban_blocks` huge) | Rejected at load or bounded | pass (bounds at load) |

## Batch 10: "Make Bull lose money" (economic attacks, griefing)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 91 | Fee-poisoning: make bitcoind's estimator spike so payouts overpay fees | Fee caps defer | todo |
| 92 | Many users go offline deliberately to force on-chain payouts (Bull pays nothing; users pay fees) | Fees come out of user outputs; no Bull loss | todo |
| 93 | User aims for the payout to drain the hot wallet faster than top-ups (cash-flow DoS) | Payouts wait for funds; no crash | todo |
| 94 | User times exit plus payout around a reorg | 100-conf sweep requirement | todo |
| 95 | User keeps coins just above `min_payout` with a high-fee batch so their output goes below dust | Bitcoind refuses → batch stuck → must not block others | see #34 |
| 96 | Operator `max_fee_share` too low during congestion → payouts deferred indefinitely | Alert; operator raises the cap | todo |
| 97 | Unrolled-round griefing: one exit quarantines a whole round's payouts | Accepted: manual review queue | todo |
| 98 | User requests nothing; Bull still pays the batch fee share? | No: subtract-fee-from-outputs | todo |
| 99 | Bull's swept funds are less than payouts owed (I1 per round) | I1 refuses the excess claim | implemented (I1 in claim) |
| 100 | Change output from the payout wallet reused across batches (wallet address reuse) | bitcoind generates fresh change | todo |

## Batch 11: "Humans and deployment" (misconfiguration, supply chain)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 101 | Sidecar pointed at the wrong captaind DB (another network) | Network/address checks fail → no pay | todo |
| 102 | Two captainds share one DB schema | Unsupported; leader lock still single sidecar | todo |
| 103 | Payout wallet on the same bitcoind as other wallets (rpcwhitelist cannot limit wallets) | Deployment rule: payout wallet alone | todo |
| 104 | Operator runs the sidecar as the postgres superuser | Works, but least privilege lost: deployment check | todo |
| 105 | `grace_blocks` set to 0 | Pays users who would have refreshed; config floor | pass (mainnet floor) |
| 106 | `sweep_min_confs` set to 1 | Reorg risk; config floor | pass (mainnet floor) |
| 107 | Compromised crate in the dependency tree | `Cargo.lock` + `cargo audit` | todo |
| 108 | Config committed to the public repo | `.gitignore`; secrets only via the deploy env | todo |
| 109 | Restoring a DB backup that predates payouts | Payout ledger lost → the same coins look unpaid → double pay! | pass (`db-restore`: journal re-marks the coin spent and rebroadcasts the journaled tx) |
| 110 | Operator deletes rows from `sidecar.payout` | Same as 109 | pass (`tamper-payout`: coin stays spent, no repay) |

## Batch 12: "Strange times" (scale, upgrades, compatibility)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 111 | captaind upgrade changes the VTXO encoding | Decode fails → quarantine; schema guard first | todo |
| 112 | captaind adds a new spend path that is not conditional | Upgrade-gate audit | todo |
| 113 | Second ships fallback refresh: re-issue and payout both happen | Must disable the sidecar or teach it re-issued coins | todo |
| 114 | Very long downtime of the sidecar (months) | Catches up in batches | todo |
| 115 | Block height near i32 max for bans | Bounded | pass (`ban_blocks` ≤ 10000) |
| 116 | Taproot address format changes / new network | Network-typed addresses | todo |
| 117 | User wallet from another Ark client (not Bark) with a different key derivation | Paid to the coin's key regardless | todo |
| 118 | Coin whose user key is a MuSig aggregate (shared wallet) | Paid to the aggregate key; spending needs both | todo |
| 119 | Sweep confirmed but captaind never recorded `onchain_spent_txid` | Coin waits forever: alert after N blocks | gap: alerting |
| 120 | Second changes `vtxo_txid` / `onchain_spent_txid` semantics | Upgrade gate; quarantine/wait only, never pay | todo |

## Batch 13: "The user pulls the emergency exit" (unilateral exits)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| X1 | Exit started before expiry; leaf confirms | Never paid (`confirmed_height`) | pass (#11) (suite: `exit-full`) |
| X2 | Exit started before expiry but only **partially progressed** (tree txs confirmed, leaf not) when expiry hits; watchman sweeps the rest | Never paid automatically; quarantine; user's exit fails: who holds the coin's value? | pass (partial unroll → quarantine; value held by Bull pending manual review) (suite: `exit-partial`) |
| X3 | Exit started **after expiry, before the sweep** (races the watchman) | Never paid while the race is open; the outcome decides | todo |
| X4 | Exit started from a stale device **after the payout** | Exit txs invalid (funding already swept) | pass (stale client → `vtxo-swept`) |
| X5 | Exit of coin A, claim of coin B in the same round | B quarantined if A unrolled first; B paid only if the round was swept wholesale (then A cannot exit) | todo |
| X6 | Leaf confirmed, user never claims the CSV output | Sidecar ignores it (`confirmed_height`) | pass (#11) |
| X7 | Exit of an arkoor-received coin (deeper chain) before expiry | As X1 | pass (as X2: arkoor coin, depth 2) |
| X8 | Exit **started then cancelled** before the final tx: part of the tree stays on-chain | Round partially unrolled: other coins quarantined although nobody exited fully | pass as a full exit (single-leaf round: the cancel came too late); the partial case is X2 (suite: `exit-partial`) |
| X9 | Exit **blocked**: no on-chain funds for CPFP, nothing confirms; the coin expires and the round is swept wholesale | Sidecar pays it; the user's client is stuck in an "exiting" state (UX) | pass (blocked exit; round swept wholesale; paid on-chain) (suite: `exit-blocked`) |
| X10 | Two users of one round exit concurrently | Both exits complete; no payouts for the round | todo |
| X11 | Watchman punishes an exit of an already-forfeited coin | Sidecar ignores server-policy outputs | reasoned (`policy_type` filter) |
| X12 | Exit of a board coin before expiry | As X1 | reasoned (as X1) |
| X13 | Exit started while the coin is banned by the sidecar | Ban is irrelevant on-chain. Exit only possible before the sweep, and the claim needs a 100-conf wholesale sweep, so no overlap | reasoned |

## Batch 14: "One user, one journey" (bark-web on the variant-b barkd, against captaind + sidecar)

The user receives, goes offline, the coin expires, the sidecar pays it, the user comes back, sees *Paying out* then *Paid out*, sweeps, and restores on another device.

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| J1 | User returns while the payout tx is still in the mempool; barkd uses bitcoind (`scantxoutset` sees only confirmed outputs) | Coin shows *Paying out* with its amount, nothing is lost from the total | observed (`web-journey`): with a bitcoind chain source the mempool payout is not found; the coin shows *Paying out* (label only) and enters the total at 1 conf |
| J2 | Same as J1, but opened in a second browser on the same barkd (the *Paying out* list is in the first browser's local storage) | The second browser shows the same total | FAIL→fixed (client): the *Paying out* amount came from one browser's local storage; the total now counts only payouts found on-chain, so every browser shows the same |
| J3 | User clicks *Move to on-chain balance*; the sweep is in the mempool | The payout leaves *Paying out*; the total counts the money once; no second sweep offered | FAIL→fixed (bark fork, `web-journey`): `scantxoutset` ignores mempool spends, so the swept payout stayed listed and *Move to on-chain balance* stayed offered; outputs spent in the mempool are now skipped |
| J4 | User clicks *Move to on-chain balance* twice before the sweep confirms (or with a higher fee rate) | One sweep; one expiry-payout movement in history | FAIL→fixed (same fix, `web-journey`): a second sweep at 50 sat/vB replaced the first and recorded a second expiry-payout movement; now refused with “no expiry payouts to sweep” |
| J5 | The payout was swept by another client (CLI, second barkd on the same seed); the first browser still lists the coin as *Paying out* | *Paying out* clears; no phantom balance | FAIL→fixed (client): the stored id came back as *Paying out* (amount in the total) once the payout was swept elsewhere; ids are dropped once their payout is found, and stored ids no longer add to the total |
| J6 | User restores the seed on a new device after the payout, before the sweep | The payout is visible and sweepable, or the docs give the recovery path | pass (`web-journey`): restore needs a birthday height; the restored wallet has the coin as spent and finds the payout |
| J7 | User restores the seed on a new device after the sweep | The swept amount appears in the on-chain balance | pass (`web-journey`) |
| J8 | *Paying out* shows the coin amount; the payout output is smaller (fee share) | Total drops by the fee share when the payout is found: the history explains it | observed (bark-web): history shows “Expired coin paid out on-chain −99 121” and “+98 292” on-chain; the payout fee share (495) and sweep fee (334) are not itemised |
| J9 | Payout claimed but the payout wallet is empty or fees are too high (rows stay `claimed` for hours) | Coin shows *Paying out*, the money is not shown twice or lost | todo |
| J10 | Expired coin quarantined by the sidecar (never paid) | Server keeps it spendable; refresh is refused; client shows it stuck, not *Paying out* | todo |
| J11 | Two coins share one key; only the older one has expired and been paid | Sweep movement and *Paid out* state cover only the paid coin | todo |
| J12 | Payout of a small coin (near `min_payout_sat`) cannot cover the sweep fee | Sweep error is clear; the note does not offer a button that always fails | todo |
| J13 | Server unreachable when the client checks its expired coins | Auto-refresh does not submit a paid-out coin; refused coins are skipped | todo |
| J14 | User starts an emergency exit of an expired coin from bark-web after the round was swept | Exit cannot succeed; no on-chain fees burnt; the coin is still paid by the sidecar | todo |
| J15 | User comes back during the ban wait (coin banned, not yet claimed) | Refresh refused; coin stays *Renewing* until the server marks it spent | todo |
| J16 | barkd restarts between adopting the spent state and finding the payout | Spent state survives; the payout is found after the restart | todo |
| J17 | Two devices on one seed sweep the same payout at the same time | One sweep confirms; the other device's history does not show money leaving twice | todo |
| J18 | User receives a new payment to the same Ark address after an older coin there was paid out on-chain | New coin is a normal Ark coin; the old payout stays sweepable | todo |
