# Owned Noah signet forwarding wallet

These are the service definitions qualified on the dedicated Noah test VM,
not a mainnet deployment. The pinned Noah backend and its local test-only push
patch are maintained separately; neither its patch nor private keys, cookies,
tokens, wallet archives or deployment manifests belong in this repository.

The backend occupies `127.0.0.1:3000`. Its native signet forwarding provider
requires `http://noah-barkd-signet.internal:3000`; map that name in `/etc/hosts`
to `127.0.0.2`, where the separate operator-owned fork wallet listens. Do not
weaken the backend's native network/URL validation or expose the wallet publicly.

Install qualified fork bark/barkd at `/opt/noah-forwarder/bin`, root-owned0755;
the public parent directories must be0755. Keep `/var/lib/noah-forwarder`0700,
owned by the dedicated `noah-forwarder` user. `/etc/noah-forwarder` is0750,
root-owned with that user's group; its Core cookie is0600 owned by that user.
Generate a fresh signet wallet with the native CLI, using the owned Ark and
Core tunnels. Never force-recreate an existing wallet to fix a transport error.
Back up the initialized private wallet on the Noah VM before starting barkd.

The tunnel service retains strict SSH host verification and a restricted key.
On the target, both the authorized-key `permitopen` options and sshd's matching
`PermitOpen` rule must allow the exact two destinations. A per-user rule limited
to the Ark port otherwise rejects Core with `administratively prohibited`, even
when the key permits it. Scope the additional Core allowance to the Noah VM's
source address before the general observer match, validate `sshd -t` and both
Noah/simulator effective configurations, then reload SSH. Do not grant the
simulator additional Core forwarding. The fixture unit carries the declared
owned IPs; update them only through a new explicit deployment declaration.

Before enabling backend forwarding, verify the actual native versions/hashes,
signet genesis/server key, ordinary lifetime, registered P2WPKH fallback and
prelinked keys, zero initial balances, required authentication, and404 for the
mnemonic endpoint. The baseline fixture has no allocated forwarding capital.
It creates no invoice or payment. Native backend URL/auth are private env-file
configuration; the backend never receives a phone's seed or mailbox private key.
Its bearer token controls only the separately owned forwarding wallet.

Activate the backend provider only after the simulator's checked SDK epoch,
real phone identities/registered destinations/jobs and immutable capital and
fee bounds are supplied. Verify native provider startup and health, then return
the complete readiness manifest before the first bounded LNURL payment. Keep
all existing campaign history and permanent client absences. A running zero-
balance forwarder is not evidence of forwarding, payout or user recovery.

Qualified checks: native wallet creation, authenticated Ark/fallback/balance
reads, unauthenticated401, mnemonic404, target record plus eight linked keys in
one read-only DB snapshot, independent Core chain/index reads, and
`systemd-analyze verify` on the installed units. Actual Lightning journeys,
forwarder crash/disappearance liabilities and seed recovery remain separate
required signet tests.
