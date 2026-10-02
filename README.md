# abandon-ship-server

Sidecar for a Bull-run [captaind](https://gitlab.com/ark-bitcoin/bark) (Second's Ark server). When a user's Ark coin expires unrefreshed and the user stays away past a grace period, it pays the coin's value on-chain to the coin's own key, exactly once.

Interim until Second ships fallback refresh. Requires no captaind code change. Design: *Ark expired-coin handling* (§3 sidecar, §5 safety).

## What it does, per tick

1. **Settle:** rebroadcast stored payout txs and mark confirmed ones.
2. **Select** candidates from captaind's DB: expired, past the grace period, spendable pubkey coins, not paid, not quarantined.
3. **Validate the coin against the chain (T1).** Decode the stored VTXO and run `Vtxo::validate(&anchor_tx)`. Amount, key and expiry come from the validated VTXO, not from DB columns.
4. **Check the sweep on-chain (T2):**
   - the round's funding output is spent;
   - the spender pays only to the configured sweep addresses (ignoring OP_RETURN and P2A anchors), the same rule captaind's watchman uses;
   - it has at least `sweep_min_confs` confirmations.

   A non-sweep spender (partial unroll) quarantines the coin.
5. **Check nothing is in flight:** skip the coin while a round participation references it.
6. **Ban** by writing captaind's own `banned_until_height` column, as its admin `BanVtxo` does (T4; bounded to fit i32). Wait `ban_wait_secs`. This step is liveness only.
7. **Claim atomically,** in one transaction, serialised per round:
   - flip `spend_state` to spent iff still spendable;
   - check the DB amount equals the validated amount;
   - enforce I1: round payouts ≤ the funding output;
   - insert the payout row.
8. **Pay:**
   - one batched tx to BIP86 `tr(coin_pubkey)`, with the fee taken from the outputs;
   - **verified before storing (T7/T8):** exact outputs, at most one change output that must be ours, feerate and fee-share caps;
   - stored before broadcast; claims are never reverted.

Any per-coin problem quarantines that coin; the loop carries on (T3). The invariant check (I2) runs after every tick, and a violation stops the process.

Startup refuses to run if another instance holds the leader lock (T9), or if captaind's schema version is not allowlisted (T10).

## Additional guards (from testing; see `docs/observations.md`)

- **Fee gate:** no claims unless bitcoind's real estimate (`estimatesmartfee`, no fallback rate) is within `max_fee_rate_sat_vb`. The payout is funded at exactly that rate.
- **Per-coin fee rule:** pay a coin only if its fee share is at most `max_fee_pct_per_payout` (default 20%) of its value and at least 330 sat remain. Otherwise leave it alone, so its owner can still refresh it.
- **Payout journal:** an append-only, fsynced file on the sidecar's own disk (`journal_path`). A journaled coin is never paid again. If one is live again in captaind (DB restore), it is re-marked spent and quarantined.
- **Pay-time re-derivation:** each claimed row's address and amount are re-derived from the chain-validated VTXO before paying. Any mismatch stops the process.
- **Our ban must be intact:** the claim requires the exact ban the sidecar set. An operator unban restarts the wait (prevents H2).
- **No piling up:** no new claims while unpaid claims exist; at most `max_batch` claims per tick.
- **Unclaimed delegated-refresh outputs (A1)** go to manual review: they cannot be fully verified until their owner returns.

## Why it cannot pay twice

- **Every captaind spend path** commits a conditional `spend_state='spendable'` update before releasing a signature, preimage or broadcast. The claim uses the same condition.
- **Payouts happen only after a wholesale sweep** verified on-chain and buried at least 100 blocks deep, so no exit tx can then be valid.
- **Destinations and amounts come from the chain-validated VTXO.**

Verified against bark master `8c29e300c`. Re-verify on every captaind upgrade.

Security: `docs/threat-model.md`. Least-privilege setup: `docs/deployment.md`.

## Run

```sh
cp config.example.toml config.toml   # edit
cargo run --release -- config.toml           # loop
cargo run --release -- config.toml --once    # single tick
RUST_LOG=info cargo run -- config.toml
```

The sidecar creates its own `sidecar` schema in captaind's Postgres (`migrations/0001_sidecar.sql`). It needs a funded bitcoind wallet (`bitcoind.url` points at it) and no access to captaind's admin gRPC.

## Status

Scaffold, not tested against a live captaind yet. Open items:

- [ ] regtest stack (`regtest/`) and the test suite from the server test plan (`tests/`)
- [ ] RBF fee bump spending the same inputs
- [ ] confirm on regtest:
  - `onchain_spent_txid` is set for swept funding outputs;
  - `Vtxo::validate` passes for spendable round and arkoor coins as stored by captaind;
  - the refinery table name;
  - the role grants
- [ ] stale participations: decide when a non-forfeited participation stops blocking a payout
