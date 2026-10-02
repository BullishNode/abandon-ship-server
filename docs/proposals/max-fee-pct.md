# Proposal: max fee per payout (`max_fee_pct_per_payout`)

Status: proposal. Patch: `max-fee-pct.patch` in this folder (not applied). It builds, and the 8 unit tests pass against the tree as of 2026-10-01. That tree already includes the journal and the O23 fee gate (an `estimatesmartfee` rate within the cap or no claims, the payout funded at exactly that rate, no fallback rate). This proposal builds on the O23 gate.

## Decision

A coin is paid on-chain only if **its fee share is at most 20% of its value and at least 330 sat remain**. The check runs at the current feerate **before the ban and the claim**, and again on every tick until the claim. A coin that fails is never banned or claimed, so it stays `spendable` and the user can still refresh it. It is checked again every tick and is paid once fees fall.

This replaces the static `min_payout_sat` floor and the batch-wide `max_fee_share`.

## Reasoning

**1. Per output, with a per-output fee bound.** bitcoind's `subtract_fee_from_outputs` gives every payout output the same share, `fee / n`. The worst case is a lone output that carries the whole tx:

- overhead 10.5 vB;
- 2 P2WPKH inputs, 2 × 68 vB;
- 1 P2TR output, 43 vB;
- change, 31 vB.

That is 220.5 vB, so we use **`LONE_OUTPUT_VB = 230`**. In any larger batch an output's share is smaller. So `share_bound = ceil(rate × 230 vB)` is an upper bound for every output. It needs no knowledge of the other outputs and no build first.

- At claim time the rule is per **coin**. This is conservative, because summing coins at one address only lowers the fee ratio.
- At pay time the rule is per **output**, i.e. per address sum.
- On the built tx the rule is checked exactly: each output's real share is `expected − actual`.

**Drop the output, not the batch.** With an equal split, the smallest output is always the first to break either rule. So:

- outputs that fail the pre-build check are left out and stay `claimed` for a later tick;
- if a build or verify still fails (bitcoind dust error, or a real share above the bound because the wallet needed 3 or more inputs), the smallest output is dropped and the build retried, at most 3 times per tick.

Nothing loops unbounded. No user can block other outputs: the bound depends only on the feerate and the wallet's inputs, never on other users' coins.

**2. 20% alone does not prevent dust.** At 0.1 sat/vB the share is 23 sat. A 340-sat coin passes 20% (23 ≤ 68), but 317 sat would remain, below dust. The same check therefore also requires `value ≥ share + 330`. Both conditions are monotone in value, so they combine into one minimum:

`min_payable = max(ceil(100 × share / pct), share + 330)`

The percentage dominates above about 0.36 sat/vB. The static floor is removed.

| feerate (sat/vB) | share bound | min payable at 20% |
| --- | --- | --- |
| 0.1 | 23 | 353 (dust rule) |
| 1 | 230 | 1,150 |
| 5 | 1,150 | 5,750 |
| 10 | 2,300 | 11,500 |
| 50 (cap) | 11,500 | 57,500 |

(The old static floor was 7,830 sat whatever the fees.)

**3. Check before the claim, with one feerate per tick.** The O23 gate already reads `estimatesmartfee(payout_conf_target)` once per tick. That same rate now drives:

- the candidate filter;
- the per-coin gate before the ban and the claim;
- the pay-time filter;
- the build itself, through an explicit `feeRate` (already done by O23).

A coin claimed in a tick is therefore payable in that same tick: no spike can fall between its claim and its payout. What a spike can still do:

- **Between ban and claim** (the ban wait): the gate fails, so the coin is not claimed. The ban lapses on its own (`ban_blocks`), and the coin can then be refreshed again, or claimed later.
- **After the claim, but the payout failed** (bitcoind error, or the coin was dropped): the coin stays `claimed` and its output waits. Claims are never undone: flipping `spent` back is the double-pay risk that I2 guards against. It is paid when the feerate falls under its threshold. It was economical when claimed, so this is a delay, not a strand.
- **No estimate, or an estimate above the cap:** no claim and no payout this tick (O23; no fallback rate, by owner ruling). In-flight txs are still settled.

**4. Starvation.** `db::candidates` adds `amount >= min_payable`. Coins that are too small never enter the `max_batch` window, so they cannot crowd out payable coins (#62). They are no longer quarantined: they are re-evaluated every tick at the new feerate.

**5. Edge cases.**

- **Single-output batch:** this is the 230 vB bound itself, so it is covered.
- **Aggregated outputs:** coins at one address that each fail alone, but would pass together, are not claimed. This is accepted to keep the gate per coin and simple.
- **Fee spikes:** see point 3.
- **Coins never economical** (below 353 sat at any feerate, or below about 1,150 sat at 1 sat/vB): they are never banned or claimed. They stay refreshable when the user returns (honour-on-return, O1). That is fine: the user loses nothing that on-chain fees would not have eaten.

## Algorithm

```
tick:
  settle_inflight()
  reassert journaled coins that are spendable again      # whole journal (see patch notes)
  rate  = estimatesmartfee(conf_target)                 # O23: none or > cap -> no claims/payouts
  share = ceil(rate_kvb * 230 / 1000)                   # rate_kvb = ceil(rate_sat_vb * 1000)
  min   = max(ceil(100*share/pct), share + 330)
  for c in candidates(amount >= min, LIMIT max_batch):  # DB amount = pre-filter
      ...validate (T1/T2)...
      if not affordable(c.validated_amount, share): Wait   # no ban, no claim
      ...ban, ban wait, claim (unchanged)...
  pay_claimed(rate)

pay_claimed(rate):
  outputs = sum claimed coins per address
  keep outputs with affordable(sum, share); the rest stay claimed
  sort outputs by value, largest first
  repeat up to 3 times:
      tx = walletcreatefundedpsbt(outputs, feeRate = rate, subtract fee from all)
      verify: feerate <= cap; for each output: affordable(expected, expected - actual)
      ok   -> mark_signed, journal, broadcast; done
      fail -> drop the smallest output (it stays claimed); retry

affordable(v, s) = 100*s <= pct*v  and  v >= s + 330
```

## Config

| Key | Default | Change |
| --- | --- | --- |
| `max_fee_pct_per_payout` | `20` | **new**: integer 1..=99 |
| `max_fee_rate_sat_vb` | `50.0` | **kept**: absolute ceiling against a broken or poisoned estimator (T7, O23) |
| `payout_conf_target` | `6` | kept: now only feeds `estimatesmartfee` |
| `min_payout_sat` | — | **removed**: replaced by the dynamic `min_payable` |
| `max_fee_share` | — | **removed**: implied, because each output's share ≤ pct means the total fee ≤ pct of the total |

These stay as code constants, not config: `LONE_OUTPUT_VB = 230`, `P2TR_DUST_SAT = 330` and `MAX_BUILD_ATTEMPTS = 3`. A config that still lacks `max_fee_pct_per_payout` fails at load. serde ignores the old keys.

Deployment notes:

- The payout wallet's `-mintxfee` must not exceed the relay floor. Core 30+ defaults are fine. Otherwise an explicit low `feeRate` is refused.
- `regtest/sidecar.toml` is git-ignored, so the patch does not touch it. Edit it by hand: remove `min_payout_sat` and `max_fee_share`, and add `max_fee_pct_per_payout = 20`. At the seeded regtest feerate (`regtest/seed-fees`), coins below about 1,150 sat × rate (in sat/vB) are no longer banned or claimed. Re-target tests that used 400-sat coins.
- A batch that keeps failing after 3 drops means the payout wallet is fragmented: consolidate it.

## What the patch changes

- **`src/checks.rs`:** adds `P2TR_DUST_SAT`, `LONE_OUTPUT_VB`, `fee_share_bound`, `affordable` and `min_payable`. `verify_payout` takes `max_fee_pct` and checks every output's exact share; the batch share check is gone.
- **`src/db.rs`:** `candidates(.., min_amount, ..)` filters on `v.amount`.
- **`src/main.rs`:**
  - the O23 per-tick rate now also feeds `share` and `min_payable`;
  - the gate before the ban (`Wait`, no longer `Quarantine`);
  - `pay_claimed` filters per output and retries without the smallest;
  - the new `build_verified` helper.
- **`src/db.rs` + `src/journal.rs`:** the journal restore check (re-mark spent + quarantine) moves out of the candidate loop into a pre-pass over all journaled ids (`spendable_among`). Without that move, the amount filter would hide a restored small coin from the check, and it could be paid twice.
- **`src/config.rs`, `config.example.toml`:** the knob changes above, plus 2 config tests.
- **`docs/threat-model.md` (T7) and `tests/edge-cases.md`:** row updates and a new batch.

## Test cases for `tests/edge-cases.md`

These are in the patch as Batch 14, since Batch 13 now holds the exit cases. It also updates rows #13, #34, #42, #62, #90, #95 and #96.

| # | Case | Expected |
| --- | --- | --- |
| 121 | Coin exactly at `min_payable`, and one sat below | At: banned, claimed and paid. Below: never banned or claimed; still `spendable` and refreshable |
| 122 | Feerate rises during the ban wait | No claim; the ban lapses; the coin is refreshable again, or claimed when fees fall |
| 123 | Feerate rises after the claim (payout failed or deferred) | Output stays `claimed` and is left out; the others are paid; it is paid later |
| 124 | Single-output batch at the threshold, wallet funds it with one input | Real share ≤ the bound; paid |
| 125 | Lone output funded by 3 or more small wallet UTXOs | Verify or dust error; the output is dropped and stays `claimed`; warning → consolidate |
| 126 | Two coins to one address, each too small alone | Neither claimed (accepted) |
| 127 | No estimate | No claim, no payout (O23) |
| 128 | Estimate above cap | No ban, claim or payout; in-flight still settled |
| 129 | 0.1 sat/vB, 340-sat coin | Passes 20% but fails the dust rule; the minimum is 353 (unit test) |
| 130 | Built tx feerate | Equals the tick's rate (`feeRate`); each output's share within the rule |
| 131 | DB `amount` lies high on a small coin | The validated amount fails the gate → `Wait` (DB writer is out of scope) |
| 132 | Old config without the new key | Load fails loudly (unit test) |
| 133 | DB restored; a small paid coin is spendable again | Re-marked spent and quarantined (whole-journal check, not the filtered window) |
