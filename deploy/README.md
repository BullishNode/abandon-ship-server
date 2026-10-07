# deploy: signet and mainnet

The regtest stack (`regtest/`) on a real network, with the same images. There is one compose file and one set of templates; `networks/<stack>.env` is the only difference between signet and mainnet.

| Piece | Role |
| --- | --- |
| `networks/signet.env`, `networks/mainnet.env` | network flags, captaind timing, sidecar policy |
| `templates/*.toml.tmpl` | captaind, watchmand and sidecar configs |
| `render.sh <stack>` | writes `state/<stack>/` (git-ignored): secrets on first run, then the configs |
| `dc <stack> …` | `docker compose` with project `abandon-<stack>` and that stack's env files |
| `sidecar.Dockerfile` | builds the sidecar from this repo |

Each stack has its own compose project, so its volumes (`abandon-signet_bitcoin`, `abandon-mainnet_bitcoin`, …), secrets, journal and payout wallet are separate. Switching means running `./dc mainnet …` instead of `./dc signet …`, on mainnet's own host.

## Bring-up

```sh
./render.sh <stack>                       # secrets + captaind.toml
./dc <stack> up -d bitcoind postgres
# payout wallet (the only wallet on the node); its address is also watchmand's sweep target
./dc <stack> exec bitcoind bitcoin-cli <net> -rpcport=8332 -rpcuser=… -rpcpassword=… \
    createwallet payout false false "" false true true
#   getnewaddress sweep bech32m  ->  state/<stack>/sweep.env: SWEEP_ADDRESS=…
./render.sh <stack>                       # watchmand.toml + sidecar.toml
# wait for initialblockdownload=false
./dc <stack> up -d cln captaind watchmand
# fund the captaind rounds wallet: ./dc <stack> exec captaind captaind --config /config/captaind.toml rpc wallet
# fund the watchman wallet
./dc <stack> exec -T postgres psql -U postgres -d bark-server-db -c 'CREATE SCHEMA sidecar;'
./dc <stack> exec -T postgres psql -U postgres -d bark-server-db < ../migrations/0001_sidecar.sql
./dc <stack> --profile sidecar up -d sidecar
```

Every port binds to localhost. Reach captaind (3535) through an SSH tunnel until a TLS proxy is in front of it.

## Signet

- Host `abandon-signet` (LunaNode m.8, Ubuntu 24.04, 150 GB data disk at `/var/lib/docker`). Firewall allows SSH from the admin IP only.
- Chain: the default public signet, which Second's `ark.signet.2nd.dev` also uses. Wallets read from `https://esplora.signet.2nd.dev`.
- Coins live 144 blocks (~1 day). One receive → expiry → payout → restore cycle takes 1–1.5 days.
- Signet coins come from faucets (rate-limited) or from Second.

## Mainnet (planned; not deployed)

Same files, `networks/mainnet.env`. Differences that are settings, not code:

| Setting | Signet | Mainnet | Source |
| --- | --- | --- | --- |
| `vtxo_lifetime` | 144 | 4320 (30 days) | `captaind.default.toml` |
| `vtxo_exit_delta` | 12 | 144 | same |
| `round_interval` | 30s | 10s | same |
| sidecar `grace_blocks` / `sweep_min_confs` | 12 / 6 | 1008 / 100 | sidecar mainnet floors are 144 / 100 (`src/config.rs`) |
| `min_payout_sat` | 1000 | 10000 | `config.example.toml` |
| `ln_receive_anti_dos_required` | false | true | |

What mainnet needs beyond the files:

1. **Its own host.** Real-money keys never share a VM with signet. LunaNode `s.8` or larger, with a **~1.2 TB SSD** (chain plus `txindex`, `blockfilterindex` and `coinstatsindex`; the sidecar needs `txindex`). The LunaNode API creates HDD volumes only, so create the SSD in the web panel and attach it with the CLI. Initial sync takes about 1–2 days.
2. **Bitcoin Core and CLN on mainnet.** Same images. CLN needs inbound 9735 open and funded channels with inbound and outbound liquidity for Lightning receive and send.
3. **Secrets.**
   - captaind's mnemonic is created on first start in the `captaind` volume, and watchmand copies it. Back it up offline before funding.
   - `secrets.env` passes the RPC and Postgres passwords on command lines. Move to `rpcauth` and a Docker secret before mainnet.
4. **Backups.** Nightly `pg_dump` and an off-host copy of `state/mainnet/journal/payouts.journal` (never truncated). Do a restore drill (`docs/deployment.md`, "Restoring captaind's database") before going live.
5. **Exposure.** A TLS proxy in front of captaind 3535 for wallets. Postgres, bitcoind RPC, the admin RPC and CLN gRPC stay private.
6. **Release gate.** Run the full regtest suite against the exact image digests in `compose.yaml`, run one complete signet cycle, then a capped pilot (the Oct 2 review verdict is "not ready for a mainnet pilot"; its blockers must be closed first).
7. **CLN hold plugin.** The `cln-hold` image runs the plugin's debug build. Check with Second before mainnet.
