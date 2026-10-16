# Deployed Track A runtime source snapshot

This preserves the exact Dockerfile and source manifest used to package the currently deployed two-step server image. The runtime-path correction is present. The older base image is an inherited layer dependency, not the current deployment identity.

`DEPLOYED-SOURCE.json` records the observed image, server and retained client identities, and the relevant container settings. Server binaries use the recorded clean server commit. The retained client binaries use an older Git base plus a 37-path source overlay, preserved on the named work branch. They must not be described as binaries from the clean base alone.

The Dockerfile and `SOURCE_MANIFEST.json` are verbatim packaging inputs. The source manifest retains its original packaging-time qualification statement. A later reconciled operational window started this image; this snapshot does not grant a new deployment or qualification.

The runtime settings are a documented extraction of the actual Docker-create arguments. There was no original captaind/watchmand Compose file for this window. Private configuration files and state remain operator-managed and are not supplied here. The earlier Core bootstrap templates elsewhere in this directory remain separate.

Fresh read-only observations found no web process or web container on the owned target, and no static assets in the inspected running-image paths. The web candidate is not represented as deployed. See the manifest for the exact limits of this observation.

This source preservation changes no running service, wallet state, funding declaration, payout, signed expiry or campaign pin. It is not a reproducible build receipt for the inherited base image, nor a production-readiness claim.
