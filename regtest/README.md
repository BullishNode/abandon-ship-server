# regtest

To do: a compose stack, separate from `~/bark-local`, with:

- captaind, watchmand, bitcoind, CLN (hold plugin), postgres, barkd;
- short `vtxo_lifetime` (about 300 blocks) and `sweep_interval` 30s, so expiry → sweep → payout runs in minutes;
- a funded `payout` bitcoind wallet.

This repo is public: test-only keys and passwords, nothing else.
