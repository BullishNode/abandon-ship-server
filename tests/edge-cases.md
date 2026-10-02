# Edge cases

Status: `todo` · `pass` · `fixed` (bug found and fixed) · `n/a` (not testable on this stack, reasoned instead) · `gap` (known, not handled). Scenario names refer to `regtest/`. DB writers, compromised hosts and malicious operators are out of scope (`docs/design.md`) and not listed.

## Batch 1: "How could the sidecar pay twice?" (races and state)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 1 | Delegated refresh stored before the ban; its round finalises after the claim | Exactly one wins; the loser fails cleanly | pass |
| 2 | Interactive refresh mid-round (submit phase) when the claim runs | The ban + wait prevents a halted round; one winner | fixed: a claim in the submit window crashed captaind; banned coins cannot be submitted and the claim needs our ban intact (`h2-probe`) |
| 3 | Scheduled delegated refresh registered pre-expiry with a height past expiry | Server rejects it at submit; it does not block payout forever | pass |
| 4 | Offboard session opened before the ban, finished after the claim | One winner | n/a: a ban blocks offboard start; open sessions end within `offboard_session_timeout` < wait |
| 5 | Lightning send phase 1 on an expired coin, concurrent with the claim | One winner | n/a: no LN channel on this stack; LN phase 1 commit is conditional (code) |
| 6 | Two sidecar instances started together | Second exits on the leader lock | pass (`infra-postgres`) |
| 7 | Crash after the claim commit, before building the tx | Restart pays once | pass (`crash-signed`) |
| 8 | Crash after `mark_signed`, before broadcast | Restart broadcasts the same tx | pass (`crash-signed`) |
| 9 | A built tx is never stored (`mark_signed` fails) | That tx is never broadcast; the wallet does not double-spend later | pass (only stored txs are broadcast) |
| 10 | Payout tx dropped from the mempool | No second, different payout for the same coin | fixed (same tx rebroadcast) |

## Batch 2: "I am the attacker" (malicious user)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 11 | User starts a unilateral exit before expiry; leaf confirms | Never paid | pass (`exit-full`) |
| 12 | One user unrolls part of the tree; every other coin in the round is then a "non-sweep" | The round is quarantined: no double pay, but a payout DoS on the others | pass (`exit-full`) |
| 13 | User splits into many dust coins | Below `min_payout_sat`: not paid; no fee drain | pass (`fee-pct-rule`) |
| 14 | Coin at max arkoor depth | Pays like any other | todo |
| 15 | User refreshes right after losing the claim race | Refused by captaind | pass (`happy-single`) |
| 16 | User restores from seed after the payout | Coin not resurrected | pass (`unclaimed-delegated`) |

## Batch 3: "Ops is having a bad day" (infrastructure)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 21 | Postgres down mid-tick | Tick errors, retried; no half state | pass: exits, supervisor restarts (`infra-postgres`) |
| 22 | bitcoind down | Tick errors, retried | fixed: wallet checked every tick (`infra-bitcoind`) |
| 23 | Payout wallet empty | Claims wait as `claimed` (at most `max_batch` payable ones); no crash loop | pass |
| 24 | Fee estimate spikes | The per-coin fee rule defers the batch | pass |
| 25 | Watchman down for a long time | No sweep, so nothing paid | pass |
| 26 | captaind upgraded (schema version changes) | Sidecar refuses to start | pass (`schema-version`) |
| 27 | Wrong `network` in config | Refuses (address network mismatch) | pass |
| 28 | Wrong `sweep_addresses` in config | Must NOT mass-quarantine coins permanently | fixed (wait + warn) |
| 29 | DB role missing a grant | Claim fails atomically, no partial state | pass |
| 30 | Operator flips a paid coin back to spendable by hand | The invariant check stops the sidecar | pass |

## Batch 4: "Bitcoin is weird" (chain level)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 31 | Reorg removes the sweep before `sweep_min_confs` | Wait; no claim | pass |
| 32 | Reorg removes a confirmed payout tx | Same tx re-mined or rebroadcast; no duplicate | pass |
| 33 | Payout tx stuck at low fee | Known gap: no RBF yet | gap |
| 34 | Fee subtraction pushes an output below dust | Batch must not get stuck forever | fixed (`min_payout_sat` floor, per-coin fee rule) |
| 35 | Batch hits standardness size limits | `max_batch` keeps it valid | n/a (`max_batch`) |
| 36 | **Several coins share one pubkey** (same Ark address paid twice) | One output per address carrying the summed amount, or separate outputs; never a collapsed amount | fixed (`shared-address`) |
| 37 | Sweep tx spends outputs of several rounds | Still a sweep | pass |
| 38 | Anchor tx not found (no txindex / pruned) | Infra error, not quarantine | fixed (wait) |
| 39 | Board coin (anchor = board tx) expires and is swept | Paid like a round coin | pass |
| 40 | Sweep pays to the sweep address plus a P2A anchor | Still a sweep | pass (unit test) |

## Batch 5: "Outside the box" (time, humans, money, future)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 41 | User returns exactly at the expiry + G boundary | One winner | todo |
| 42 | Coin exactly at `min_payout_sat` | Paid (inclusive) | pass |
| 43 | Transient state (spender not indexed yet) is classified as permanent quarantine | Quarantine only for permanent faults | fixed (wait) |
| 44 | Lightning-received coin (server was the sender) expires | Paid to the user's key | todo |
| 45 | Expired HTLC coins (Lightning in flight) | Not paid by the sidecar; their own expiry rules apply | n/a (`policy_type` filter) |
| 46 | Thousands of coins expire at once | Paid in successive batches; no tick blowup | n/a (`max_batch`) |
| 47 | The same user is paid in two batches to the same `tr(key)` | Address reuse: privacy note only | pass (privacy note) |
| 48 | A payout lands on a key the user's wallet no longer scans (key index beyond its gap) | Documented recovery path | pass |
| 49 | Sweep and payout confirm in the same block as a user exit attempt | The exit is invalid after the sweep | todo |
| 50 | Bull disappears after payouts | Users can still spend `tr(coin key)` with the seed | pass (`unclaimed-delegated`) |

## Batch 6: "Races and timing" (requested follow-up)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 51 | TOCTOU: a participation is inserted after the last in-round check, before the claim | No double pay; risk is a halted round, liveness only | fixed (as #2) |
| 52 | captaind's spend transaction for the coin is open (uncommitted) when the claim runs | Claim blocks on the row lock, then returns Lost | pass (`race-held-lock`) |
| 53 | Claim transaction open (uncommitted) when captaind tries to spend the coin | captaind blocks, then its conditional update fails; nothing released | pass (`race-held-lock`) |
| 54 | Postgres connection drops; the advisory leader lock is lost; a second instance starts | Sidecar must exit on connection loss (no silent reconnect without the lock) | pass (`infra-postgres`) |
| 55 | Payout-wallet UTXO spent by an operator between build and broadcast | Stored tx invalid; must not rebuild until the old tx is provably dead | todo |
| 56 | User returns during the ban-wait window | Refresh refused, then paid on-chain: UX note | pass (`race-user-refresh`) |
| 57 | Operator re-bans or unbans the coin via captaind admin while the sidecar waits | Ban shortened; safety unaffected (the claim is conditional) | fixed (`race-operator-unban`) |
| 58 | Exit leaf confirms between the sweep-depth check and the claim | Impossible after a wholesale sweep; the claim guard on `confirmed_height` holds anyway | n/a (`confirmed_height` filter + sweep rule) |
| 59 | captaind restarts mid-round while the sidecar claims | In-memory locks lost; the DB guard is still atomic | todo |
| 60 | Payout batch timing and co-membership reveal which users were offline | Privacy note; observers can link users paid in one batch | note |

## Batch 7: "Exhaust it" (resource exhaustion, DoS)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 61 | Millions of dust coins expire at once; the candidate query scans them every tick | Bounded by `LIMIT max_batch` with an index; no OOM | fixed (small coins filtered before the limit) |
| 62 | Dust coins fill every candidate slot forever (quarantine-or-wait loop starves real coins) | Quarantined dust leaves the window; real coins still progress | fixed (as #61); gap: coins unaffordable at the current fee rate still occupy the window |
| 63 | One huge VTXO blob (max exit depth) per candidate | Decode memory bounded per coin | todo |
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
| 73 | SIGKILL between `mark_signed` and broadcast | Restart rebroadcasts the stored tx | pass (`crash-signed`) |
| 74 | OOM-kill during payout building | Same as 72/73 by stage | todo |
| 75 | Panic on unexpected data (unwrap) | No unwraps on external data; process exits non-zero, supervisor restarts | fixed (all reads are `try_get`) |
| 76 | Postgres restarts; the sidecar's connection is dead | Process must exit (and re-lock on restart), not spin forever | pass (`infra-postgres`) |
| 77 | bitcoind restarts mid-build | RPC error, tick retried; the wallet tx is not broadcast | pass (`infra-bitcoind`) |
| 78 | Disk full on the DB host | Writes fail atomically; tick retried | todo |
| 79 | Host clock jumps (NTP) | Ban age uses DB `NOW()`; heights drive everything else | todo |
| 80 | Sidecar binary built against a different `ark-lib` than captaind | Schema guard plus a decode failure → quarantine, no pay | todo |

## Batch 9: "Feed it garbage" (parsing)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 86 | Payout address from a non-pubkey policy (checkpoint, HTLC) | Filtered by `policy_type` | todo |
| 89 | Sweep tx with zero non-anchor outputs (OP_RETURN only) | Not a sweep (`paid_any` false) | pass (unit test) |
| 90 | Config values out of range (`max_fee_pct_per_payout` 0, `grace` 0, `ban_blocks` huge) | Rejected at load or bounded | pass |

## Batch 10: "Make Bull lose money" (economic attacks, griefing)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 91 | Fee-poisoning: make bitcoind's estimator spike so payouts overpay fees | The per-coin fee rule defers | todo |
| 92 | Many users go offline deliberately to force on-chain payouts (Bull pays nothing; users pay fees) | Fees come out of user outputs; no Bull loss | todo |
| 93 | User aims for the payout to drain the hot wallet faster than top-ups (cash-flow DoS) | Payouts wait for funds; no crash | todo |
| 94 | User times exit plus payout around a reorg | 100-conf sweep requirement | todo |
| 95 | User keeps coins just above `min_payout` with a high-fee batch so their output goes below dust | Bitcoind refuses → batch stuck → must not block others | fixed (as #34) |
| 96 | Operator `max_fee_pct_per_payout` too low during congestion → payouts deferred indefinitely | Alert; operator raises the cap | todo |
| 97 | Unrolled-round griefing: one exit quarantines a whole round's payouts | Accepted: manual review queue | todo |
| 98 | User requests nothing; Bull still pays the batch fee share? | No: subtract-fee-from-outputs | todo |
| 100 | Change output from the payout wallet reused across batches (wallet address reuse) | bitcoind generates fresh change | todo |

## Batch 11: "Humans and deployment" (misconfiguration, supply chain)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 101 | Sidecar pointed at the wrong captaind DB (another network) | Network/address checks fail → no pay | todo |
| 102 | Two captainds share one DB schema | Unsupported; leader lock still single sidecar | todo |
| 103 | Payout wallet on the same bitcoind as other wallets | Deployment rule: payout wallet alone | todo |
| 105 | `grace_blocks` set to 0 | Pays users who would have refreshed; config floor | pass (mainnet floor) |
| 106 | `sweep_min_confs` set to 1 | Reorg risk; config floor | pass (mainnet floor) |
| 107 | Compromised crate in the dependency tree | `Cargo.lock` + `cargo audit` | todo |
| 108 | Config committed to the public repo | `.gitignore`; secrets only via the deploy env | todo |
| 109 | Restoring a DB backup that predates payouts | Payout ledger lost → the same coins look unpaid → double pay! | pass (`db-restore`) |
| 110 | Operator deletes rows from `sidecar.payout` | Same as 109 | pass (`tamper-payout`) |

## Batch 12: "Strange times" (scale, upgrades, compatibility)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 111 | captaind upgrade changes the VTXO encoding | Decode fails → quarantine; schema guard first | todo |
| 112 | captaind adds a new spend path that is not conditional | Upgrade-gate audit | todo |
| 113 | captaind adds a fallback refresh: re-issue and payout both happen | Must disable the sidecar or teach it re-issued coins | todo |
| 114 | Very long downtime of the sidecar (months) | Catches up in batches | todo |
| 115 | Block height near i32 max for bans | Bounded | pass (`ban_blocks` ≤ 10000) |
| 116 | Taproot address format changes / new network | Network-typed addresses | todo |
| 117 | User wallet from another Ark client (not Bark) with a different key derivation | Paid to the coin's key regardless | todo |
| 118 | Coin whose user key is a MuSig aggregate (shared wallet) | Paid to the aggregate key; spending needs both | todo |
| 119 | Sweep confirmed but captaind never recorded `onchain_spent_txid` | Coin waits forever: alert after N blocks | gap (no alerting) |
| 120 | captaind changes `vtxo_txid` / `onchain_spent_txid` semantics | Upgrade gate; quarantine/wait only, never pay | todo |

## Batch 13: "The user pulls the emergency exit" (unilateral exits)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| X1 | Exit started before expiry; leaf confirms | Never paid (`confirmed_height`) | pass (`exit-full`) |
| X2 | Exit started before expiry but only **partially progressed** (tree txs confirmed, leaf not) when expiry hits; watchman sweeps the rest | Never paid automatically; quarantine; user's exit fails: who holds the coin's value? | pass: quarantined for manual review (`exit-partial`) |
| X3 | Exit started **after expiry, before the sweep** (races the watchman) | Never paid while the race is open; the outcome decides | todo |
| X4 | Exit started from a stale device **after the payout** | Exit txs invalid (funding already swept) | pass |
| X5 | Exit of coin A, claim of coin B in the same round | B quarantined if A unrolled first; B paid only if the round was swept wholesale (then A cannot exit) | todo |
| X6 | Leaf confirmed, user never claims the CSV output | Sidecar ignores it (`confirmed_height`) | pass (`exit-full`) |
| X7 | Exit of an arkoor-received coin (deeper chain) before expiry | As X1 | pass (`exit-partial`) |
| X8 | Exit **started then cancelled** before the final tx: part of the tree stays on-chain | Round partially unrolled: other coins quarantined although nobody exited fully | pass (`exit-partial`) |
| X9 | Exit **blocked**: no on-chain funds for CPFP, nothing confirms; the coin expires and the round is swept wholesale | Sidecar pays it; the user's client is stuck in an "exiting" state (UX) | pass (`exit-blocked`) |
| X10 | Two users of one round exit concurrently | Both exits complete; no payouts for the round | todo |
| X11 | Watchman punishes an exit of an already-forfeited coin | Sidecar ignores server-policy outputs | n/a (`policy_type` filter) |
| X12 | Exit of a board coin before expiry | As X1 | n/a (as X1) |
| X13 | Exit started while the coin is banned by the sidecar | No overlap: an exit is only possible before the sweep, the claim needs a deep wholesale sweep | n/a |

## Batch 14: "One user, one journey" (bark-web on the variant-b barkd, against captaind + sidecar)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| J1 | User returns while the payout tx is still in the mempool; barkd uses bitcoind (`scantxoutset` sees only confirmed outputs) | Coin shows *Paying out* with its amount, nothing is lost from the total | gap: with a bitcoind chain source the amount enters the total at 1 conf (`web-journey`) |
| J2 | Same as J1, but opened in a second browser on the same barkd (the *Paying out* list is in the first browser's local storage) | The second browser shows the same total | fixed (client: only on-chain payouts count) |
| J3 | User clicks *Move to on-chain balance*; the sweep is in the mempool | The payout leaves *Paying out*; the total counts the money once; no second sweep offered | fixed (bark: mempool-spent payouts skipped; `web-journey`) |
| J4 | User clicks *Move to on-chain balance* twice before the sweep confirms (or with a higher fee rate) | One sweep; one expiry-payout movement in history | fixed (as J3; `web-journey`) |
| J5 | The payout was swept by another client (CLI, second barkd on the same seed); the first browser still lists the coin as *Paying out* | *Paying out* clears; no phantom balance | fixed (client: ids dropped once their payout is found) |
| J6 | User restores the seed on a new device after the payout, before the sweep | The payout is visible and sweepable, or the docs give the recovery path | pass: needs a birthday height (`web-journey`) |
| J7 | User restores the seed on a new device after the sweep | The swept amount appears in the on-chain balance | pass (`web-journey`) |
| J8 | *Paying out* shows the coin amount; the payout output is smaller (fee share) | Total drops by the fee share when the payout is found: the history explains it | gap: fee share and sweep fee not itemised |
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

## Batch 15: "Partial control" (a malicious owner or client)

The attacker holds some coins and runs any client; captaind, its DB and the sidecar host are trusted (`docs/design.md`).

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 121 | Owner's small coin is claimed at a low fee rate; fees rise, it is no longer affordable and stays `claimed` | Other expired coins are still claimed and paid; the small one is paid when fees fall | fixed: a fee-stuck claimed coin blocked every new claim; now only payable claims count against `max_batch` (`fee-stuck-claim`) |
| 122 | Owner arkoor-sends an expired, swept coin before the sidecar bans it | Refused (captaind refuses arkoor of expired coins unless `allow_expired_arkoor`); paid once; never more than the coin's amount for the coin and its children | pass: refused by the client and by captaind (`attack-ban-wait`) |
| 123 | Owner arkoor-sends the coin during the ban wait | Refused; paid once | pass (`attack-ban-wait`) |
| 124 | Owner offboards the coin during the ban wait | Refused ("banned until block"); paid once | pass (`attack-ban-wait`) |
| 125 | Owner offboards an expired, swept coin before the ban | The offboard wins; the coin is never paid (`offboarded_in`, not spendable) | todo |
| 126 | One owner's partial exit unrolls a round in which many other coins were abandoned | Those coins are quarantined; payouts of other rounds go on | fixed: each quarantined coin counted against `max_quarantine_per_tick`, so one exit stopped all payouts; one unrolled round now counts once (`exit-breaker`) |
| 127 | Owner opens `max_batch` × 20 coins that wait forever at the front of the candidate window (unaffordable at today's fee) | Newer coins still progress | gap (as #62) |
| 128 | Owner sends coins to a victim's Ark address (same key) | Victim's payout output carries the sum; nobody loses | pass: one output per address carrying the sum (`shared-address`) |
| 129 | Owner splits value into coins each too small for the fee rule but affordable together on one key | Not claimed (the rule is per coin at claim time); they stay refreshable | pass: affordability is checked per coin before the ban (code) |
| 130 | Owner refreshes one of two coins that share a key; the other is paid | Only the paid coin is listed with the payout; the sweep movement counts it once | gap: the bark fork lists the payout under every expired spent coin of the key, so the refreshed coin shows *Paid out* and the sweep movement subtracts its amount too; a spend recorded by adoption is not told apart from a refresh |
| 131 | Client submits `refresh --all` with a banned coin and a fresh coin during the ban wait | Banned coin refused; the fresh coin can still be refreshed | todo |
| 132 | Owner holds a delegated (`unclaimed`) output and claims it during the ban wait | The ban holds; the sidecar's claim or the owner's spend wins, never both | todo |
| 133 | Client calls adopt-server-status on unexpired coins in a loop | Spent coins are marked spent; spendable ones unchanged; no payout effect | pass: adoption only copies the server's state (code) |
| 134 | Client calls sweep-expiry-payouts from two barkds on one seed at once | One sweep confirms; the other is rejected or replaced; one movement per barkd | todo |
| 135 | Owner exits a coin after the sidecar banned it, before the claim | Exit txs spend an already-swept funding output: invalid; coin paid once | n/a: the claim needs a sweep `sweep_min_confs` deep, after which exit txs are invalid (as X13) |
| 136 | Owner with many claimed coins at a rising fee holds `max_batch` claimed rows | Unaffordable rows do not count against `max_batch` (as 121) | fixed (as 121) |
