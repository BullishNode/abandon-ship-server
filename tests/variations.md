# Variations derived from observations (120)

Each group starts from something seen on regtest (`docs/observations.md`) and varies it. Status: `todo` unless noted.

## V-A. Spend-path races (from #52, #57, O20/H2): 20

| # | Variation |
| --- | --- |
| A1 | Claim lands 0.5 s / 2 s / 4.5 s / 6 s after "Round started" (sweep the submit window); record which runs crash captaind — **pass** (suite: `h2-probe: ban at pre/0.5/2/4.5 s, no crash`) |
| A2 | Same as A1 with 3 participants in the round; check whether the other two lose their round |
| A3 | Same as A1 with a delegated (non-interactive) participation instead of an interactive one |
| A4 | Operator unbans, then re-bans the coin with a **different** height during the wait (claim must not proceed) — **pass** (suite: `race-operator-unban`) |
| A5 | Operator re-bans with the **same** height we wrote (indistinguishable: acceptable?) |
| A6 | Ban lapses naturally (`ban_blocks` small) before the claim tick (claim must not proceed) — **pass** (suite: `race-operator-unban`) |
| A7 | Two claims for coins of the same round in one tick, while a captaind round spends one of them |
| A8 | Claim racing an arkoor cosign of the same coin (unexpired coin with `allow_expired_arkoor=true`) |
| A9 | Claim racing Lightning send phase 1 (needs an LN channel on the stack) |
| A10 | Claim racing an offboard `finish` whose `prepare` started before the ban |
| A11 | Held captaind spend tx (as #52), then rollback instead of commit: the claim must then win — **pass** (suite: `race-held-lock`) |
| A12 | Held claim tx (sidecar paused via a debugger), captaind spends meanwhile: captaind must fail |
| A13 | Restart captaind mid-wait: does the ban survive (DB) and the wait continue? |
| A14 | Restart Postgres mid-claim transaction: rollback, retry next start |
| A15 | User refreshes at exactly `expiry + G` (boundary tick) |
| A16 | User's scheduled delegated refresh never executes (poison pill): the coin is blocked from payout forever. Add an age cut-off? — (note: a scheduled refresh executed fine for an offline user, u5 round 14) |
| A17 | User submits a delegated refresh after the ban is placed (must be refused) — **pass** (suite: `race-user-refresh`) |
| A18 | Two sidecar instances against two DB replicas (leader lock is per DB: a split brain?) |
| A19 | Sidecar and captaind clocks/heights disagree by one block during the claim |
| A20 | Claim during a captaind upgrade migration (schema guard mid-flight) |

## V-B. Chain and reorgs (from O16, #31, #32): 15

| # | Variation |
| --- | --- |
| B1 | Reorg of 1/5/50 blocks **without** height decrease (competing chain), sweep included |
| B2 | Reorg that lowers the height (O16): captaind crash; sidecar behaviour while captaind is down |
| B3 | Reorg replaces the sweep with a different sweep tx (other txid): `onchain_spent_txid` is stale |
| B4 | Reorg removes the funding tx of a round itself (anchor gone) |
| B5 | Sweep tx RBF'd by the watchman before confirming (txid changes) |
| B6 | Payout tx and sweep in the same block, then that block is reorged |
| B7 | Payout confirmed, then reorged out, then a conflicting payout-wallet spend confirms |
| B8 | Mempool full / min relay fee above the payout feerate |
| B9 | `txindex` disabled on bitcoind (anchor fetch fails for all: must wait, not quarantine) |
| B10 | Pruned node |
| B11 | bitcoind restarted with `-persistmempool=0` and the payout wallet unloaded (#10 proper) |
| B12 | Sweep spends 1000 funding outputs at once (huge tx) |
| B13 | Sweep pays to two sweep addresses (config with 2 entries) |
| B14 | Sweep address rotated in watchmand config; old sweeps must still count |
| B15 | Board coin swept by a board-sweep tx shape that differs from round sweeps |

## V-C. Data integrity (DB writers are out of scope; restore cases remain) (from #17–#20, #81–#83): 15

| # | Variation |
| --- | --- |
| C1 | Valid blob of a coin from **another round** with the row id changed to match — **n/a** (DB writers out of scope) |
| C2 | Valid blob, `policy` column swapped to another user's pubkey (blob decides: check) — **n/a** (DB writers out of scope) |
| C3 | `anchor_point` column edited (blob decides: check) — **n/a** (DB writers out of scope) |
| C4 | `onchain_spent_txid` pointing at a real sweep of a different round — **n/a** (DB writers out of scope) |
| C5 | Insert a new row duplicating an existing coin's blob under another id — **n/a** (DB writers out of scope) |
| C6 | Edit `sidecar.payout.amount_sat` between claim and pay — **n/a** (DB writers out of scope) |
| C7 | Edit `sidecar.payout.address` between claim and pay (must be re-derived or checked) — **n/a** (DB writers out of scope) |
| C8 | Insert a fake `sidecar.payout` row in state `claimed` for a coin that is still spendable — **pass** (journal → stop) |
| C9 | Delete a `sidecar.ban` row mid-wait — **n/a** (DB writers out of scope) |
| C10 | Delete `sidecar.payout` rows after the payout (#110): coin already spent, so no repay? Verify — **pass** (suite: `tamper-payout`) |
| C11 | DB restored to before the claim (#109): the coin is spendable again and the payout is on-chain — **pass** (suite: `db-restore`) |
| C12 | `vtxo_history` trigger disabled by an operator — **n/a** (DB writers out of scope) |
| C13 | Signature-only forgery keeping the id (needs a crafted VTXO; may be infeasible) — **n/a** (DB writers out of scope) |
| C14 | `refinery_schema_history` edited to fake the allowed version — **n/a** (DB writers out of scope) |
| C15 | Two coins with identical blobs (same id) under different rows — **n/a** (DB writers out of scope) |

## V-D. Money and fees (from #24, #34/#95, #36): 15

| # | Variation |
| --- | --- |
| D1 | Payout of 1 coin vs 100 coins at the same feerate: per-output share |
| D2 | Coin just above / at / below the computed floor |
| D3 | Feerate cap hit with claims pending for hours: claims accumulate; then the fee drops |
| D4 | Fee spikes between claim and pay (claimed coins stranded; see max-fee proposal) |
| D5 | 50 coins aggregated to one address plus 50 distinct addresses |
| D6 | Payout wallet balance < batch total (partial pay? deferral) |
| D7 | Payout wallet funded only with unconfirmed UTXOs |
| D8 | Change output below dust |
| D9 | `max_fee_share` reached because of one huge coin and many tiny ones |
| D10 | I1: payouts for a round approaching the funding output value |
| D11 | Round where captaind took fees: funding value < Σ user amounts? Check I1 does not false-refuse |
| D12 | Coin whose amount changed through arkoor fees (validated amount vs DB) |
| D13 | Board coin with `min_board_amount`-sized value |
| D14 | Multiple payouts to the same `tr(key)` over time (address reuse) |
| D15 | Payout to an address type a light client cannot scan (tr key path) |

## V-E. Process, restart, ops (from O17, O18, O21, #21–#23): 15

| # | Variation |
| --- | --- |
| E1 | SIGKILL at each of: after ban, mid-claim, after claim, after build, after mark_signed, after broadcast |
| E2 | OOM-kill (cgroup limit) during a 500-coin tick |
| E3 | Disk full on the Postgres volume during a claim |
| E4 | Postgres failover to a replica (new session, lock re-acquired) |
| E5 | Sidecar supervisor restart loop (exits on each Postgres blip) |
| E6 | bitcoind RPC timeout (slow node) mid-build |
| E7 | Wallet locked or encrypted (passphrase) on the payout node |
| E8 | captaind down for hours while the sidecar runs |
| E9 | watchmand down for hours (no sweeps; nothing paid; alert?) |
| E10 | Log volume over 24 h with 1000 stuck coins |
| E11 | Config reload without restart (not supported: document) |
| E12 | Time to recover after a week-long sidecar outage (catch-up rate) |
| E13 | Running the sidecar as the superuser vs the least-privilege role (all paths) |
| E14 | rpcwhitelist enforced on the regtest RPC user (all calls the sidecar makes) — **pass** (suite: `all scenarios run as the whitelisted RPC user`) |
| E15 | Upgrade captaind to a new nightly: does the gate catch semantic changes? |

## V-F. Client-visible behaviour (from O13, O19, #16): 10

| # | Variation |
| --- | --- |
| F1 | Stock wallet after payout: balance, history, maintenance (needs_refresh forever) |
| F2 | Variant B client: adopt status → coin disappears; payout found; sweep |
| F3 | User restores after payout on a second device while the first still holds the coin |
| F4 | User with coins both paid and refreshable (mixed) |
| F5 | User returns during the ban wait (O19): message shown |
| F6 | User returns after payout: on-chain balance under the Ark child key |
| F7 | Wallet's gap limit vs coin-key index of the payout |
| F8 | User sends the paid coin by arkoor from a stale device |
| F9 | User exits a paid coin from a stale device (must fail on-chain) |
| F10 | Push notification timing vs payout |

## V-G. Scale and performance (from #46, #61–#70): 15

| # | Variation |
| --- | --- |
| G1 | 10k expired coins: candidate query time with and without an index on `(policy_type, spend_state, expiry)` |
| G2 | 10k quarantined dust coins: do they leave the candidate window? (NOT EXISTS cost) |
| G3 | Anchor fetch per coin: 1000 coins in one round means 1000 identical RPCs (cache) |
| G4 | Validation CPU time for depth-100 arkoor chains |
| G5 | Tick duration with `max_batch` 500 |
| G6 | Payout tx size at 500 outputs (standardness) |
| G7 | `sidecar.ban` table growth over a year |
| G8 | `vtxo_history` growth from the sidecar's updates (2 per coin) |
| G9 | Concurrency with captaind's DB load (lock contention on `vtxo`) |
| G10 | Postgres `statement_timeout` effects on long queries |
| G11 | bitcoind RPC concurrency limits (`rpcworkqueue`) |
| G12 | Memory with 500 VTXO blobs in one batch |
| G13 | Sweep with thousands of inputs: decode time |
| G14 | Network partition between the sidecar and the DB host |
| G15 | Clock drift on the DB host (ban age) |

## V-H. Upgrade and compatibility (from O10, O11, schema guard): 15

| # | Variation |
| --- | --- |
| H1 | Core 32 RPC changes (untyped calls only remain) |
| H2 | captaind schema 68 adds a new spend path |
| H3 | captaind renames `spend_state` values |
| H4 | captaind starts storing Bare (not Full) VTXOs |
| H5 | `ark-lib` version mismatch with captaind (decode failures → quarantine storm) |
| H6 | Second ships fallback refresh: sidecar and re-issue both act on one coin |
| H7 | Second ships `SettleVtxo`: migrate the claim to the RPC |
| H8 | Bark client changes key derivation (`m/350'` moves) |
| H9 | Taproot change (new script version) in payout addresses |
| H10 | Postgres major upgrade (advisory lock semantics) |
| H11 | tokio-postgres TLS required by the DB |
| H12 | Rust toolchain bump breaks reproducible builds |
| H13 | Docker image digests updated (nightly drift) |
| H14 | New watchmand claim tx shape (outputs beyond the sweep address + P2A) |
| H15 | Regtest-only config values copied into production by mistake (floors catch some) |
