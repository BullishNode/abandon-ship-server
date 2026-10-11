# Isolated signet simulation

The simulator and its two Lightning nodes run on a separate VM. The existing
fallback stack remains the target. No client seed or mailbox private key is
installed in captaind.

The default stack has no Lightning backend. To connect the dedicated simulator:

1. Install `core/{ca.pem,client.pem,client-key.pem}` and
   `hold/{ca.pem,client.pem,client-key.pem}` under the ignored directory
   `deploy/state/signet-fallback/simulation-cln/`, with private permissions.
   Certificates must authenticate the simulator node's actual host IP.
2. Add ignored `deploy/state/signet-fallback/simulation.env` containing
   `SIMULATION_LIGHTNING_HOST=<IPv4>` and
   `VTXO_POOL_TARGETS='["10000sat:8"]'`. This reserves an initial80,000sats
   of operator pool capital, plus round funding/fees. Do not count it as
   customer funding. Record opening treasury and all later injections.
3. Back up PostgreSQL and the rendered configuration. Verify there is no
   unconfirmed expiry payout. Stop the target captaind/watchmand before
   rendering, then recreate only those services on the same pinned image.
   Keep every intentionally absent client stopped; do not run the general
   wallet upgrade command.
4. Verify the image/server key, minimum net payout, node connection and pool
   balance, TLS authentication, browser/API health and the observer tunnel's
   destination. Record the resulting configuration hashes without secrets.

Allow ports9470/9471 only from the target VM. The nodes' peer ports and Core RPC
remain local to the simulation VM. The simulator host owns channel keys;
captaind receives only its node's mTLS client credentials.

Rendering validates the optional files/host and TOML before replacing the
captaind configuration. Removing the override stops future pool issuance and
removes the backend on the next controlled restart; it does not settle or
discard existing Lightning obligations. Drain/reconcile those first.

Enabling this topology is setup, not a qualified campaign. Funding, real
payments, expiry/recovery, the three-hour client ramp and restart evidence
remain independent acceptance gates.
