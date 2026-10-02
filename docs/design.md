# Design

## Threats and guards

| Attacker / failure | Attempt | Guard |
| --- | --- | --- |
| DB writer (captaind DB compromise, SQL access, bug) | Insert or edit a coin row to get paid | Chain validation of the VTXO blob, its policy and its server key; amounts and keys never taken from columns; per-round cap (payouts ≤ funding output) |
| DB writer | Forge the unsigned last step of an unclaimed output | The paid key must match the hArk leaf script in the signed parent output |
| DB writer | Edit, reset or delete ledger rows; restore an old DB | Pay-time re-derivation from the VTXO; local journal; invariant check; DB role has no `DELETE` on its tables |
| DB writer | Point the spender hint at an unrelated tx | The spender must spend the anchor on-chain, and pay only the sweep scripts |
| User | Redeem in Ark **and** get paid | Conditional claim racing captaind's conditional spends: exactly one wins |
| User | Exit **and** get paid | Payout only after a wholesale sweep `sweep_min_confs` deep; leaf-confirmed coins are never candidates |
| User | Refresh during the claim (in-flight round) | Ban, then wait longer than a round; claim requires our ban intact. A claim landing inside a round's submit window would make captaind's round persist fail and exit the process (seen on regtest when the ban is bypassed) |
| Sidecar host compromise | Drain the payout wallet; misuse RPC | Small float; `rpcwhitelist`; destinations derived from chain data |
| Fee estimator broken or absent | Overpay fees, or strand claimed coins | No estimate: no claims, no payouts. Per-coin percentage cap. No fallback rate |
| Upgrade | captaind schema or encoding changes | Schema version allowlisted, checked every tick; circuit breaker on mass quarantine |
| Crash or restart anywhere | Double pay, lost payout | Tx stored before broadcast; journal appended before broadcast; rebroadcast reuses the stored tx; rows only move forward |
| Second instance | Duplicate batches | Postgres advisory lock |

## Limitations

- **Partially unrolled rounds:** if a round's funding output is spent by a tree tx (some user exited partway), its remaining coins are quarantined, not paid. They can still be redeemed through captaind if the owner returns.
- **v0 hArk outputs:** unclaimed outputs with a v0 hash-locked leaf are quarantined (no public v0 leaf policy type in `ark-lib`).
- **Small coins:** coins below `min_payout_sat`, or whose fee share exceeds `max_fee_pct_per_payout`, are not paid on-chain. They stay refreshable by their owner.
- **No RBF bump:** a payout stuck at a low fee is rebroadcast but not bumped.
- **Client side:** captaind does not tell wallets that a coin was paid out. A wallet must ask (`GetVtxoStatus`), find the payout at `tr(coin key)`, and sweep it. Until it does, the coin still appears in its balance and refreshes of it are refused.
- **Dependence on captaind's DB:** column names, `spend_state` values and the stored VTXO encoding are captaind internals. The schema allowlist plus an upgrade check (audit every `UPDATE vtxo`, rerun `tests/regtest/`) are required before each captaind upgrade.

## Operational notes

- **captaind needs a restart policy:** rare races and some chain events make it exit.
- **The payout wallet** must be the only wallet on its bitcoind, loaded on startup, with `txindex=1`.
- **Back up the journal** separately from captaind's database.
- **DB restore:** start the sidecar before captaind (`deployment.md`).
