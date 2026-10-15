# Track A: independent signet Core bootstrap

These files bootstrap Bitcoin Core from the pinned image with wallet functionality
disabled. They do not start captaind, watchmand, Lightning, Noah or simulator traffic.
Use fresh storage and an isolated Docker network; do not copy another stack's keys,
cookies, wallets or databases.

Install under `/home/ubuntu/two-step-signet-core/`. The dedicated data volume must
already be mounted at `/srv/two-step-core`; these files do not format devices.
Choose an unused private Docker CIDR in the deployment environment, then render
only that variable into the public configuration template:

```sh
mkdir -p config
umask 077
: "${A_CORE_PRIVATE_CIDR:?Set the dedicated Docker network CIDR}"
export A_CORE_PRIVATE_CIDR
envsubst '${A_CORE_PRIVATE_CIDR}' < bitcoin.conf.template > config/bitcoin.conf
```

Export `A_CORE_PRIVATE_CIDR` for Compose as well. Keep the resulting configuration,
environment files and RPC cookie outside version control. RPC is published only
on `127.0.0.1:28332` and the dedicated Docker network. Core connects outbound to
official signet peers; no public RPC or P2P port is published. The container runs
as UID/GID 1000 and has no automatic restart policy.

Schedule `disk-guard.py` every ten seconds if using this bootstrap. It stops only
the exact Compose project/service when the dedicated mount disappears or root/data
free space falls below 42 GiB. Investigate `STORAGE_STOP` before restarting; retain
at least 40 GiB free. Preserve Core's data-volume logs; Docker's duplicate log
stream is bounded by the Compose logging configuration.

Before starting, verify the host, dedicated mount, fresh data directory, image
digest, rendered configuration and storage floor. After starting, independently
verify signet genesis
`00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6`,
`chain=signet`, unpruned operation, txindex, synchronization progress, RPC bindings
and absence of loaded wallets. A responsive RPC alone does not prove synchronization.

This directory contains four bootstrap source files, not a reproducible manifest
of the complete running Track A stack. Financial daemon images, native SDK builds,
generated observers, service templates and their exact source/binary identities
require separate manifests and qualification. Branch naming does not certify a
production release. Publishing these templates does not deploy them.
