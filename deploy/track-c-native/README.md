# Track C native service deployment source

These are the service units used by the isolated C deployment. The application is Rust code in BullishNode/abandon-ship-bark/server. C currently consumes no TypeScript or deployment implementation from BullishNode/abandon-ship-server. These files preserve C deployment source; they do not turn that repository into the C application source.

The live financial native binaries are built from af52be109466e47de96b0fed0e42c7688420a06c. The separately pinned SDK is9298d7c1e7ccb8acea138da6be64674107e6f23b. The canonical native release ref is production/track-c-direct-per-key; deployed financial fixes remain a scoped candidate with full qualification open.

The units require a separately provisioned service user, private configurations, mounted data, local Core/Postgres units and the original prestart-capture interface. Private TOML/configuration, credentials, identity seeds, database or wallet files, runtime captures and financial state are intentionally outside source control. Native binaries must be verified against an explicit release manifest before any service start.

The ExecStartPre command is a required identity/configuration capture dependency. This candidate does not include its implementation or claim these two units form a standalone installer. Financial readiness, funding, key ownership, confirmation policies and dispatch guards remain separate gates. Restart is disabled; no campaign scheduler or wallet action is provided here.

This directory is published on production/track-c-direct-per-key, based on the preserved deployment repository commit 6ced835f8cefcc90e746ab90fb286152e67625a4. Publication preserves these unit templates and does not deploy or restart services. The two service files match the original C installer templates byte for byte; the installer source SHA256 is 9b17ab1df66d0268d68e0e67314a5f3507526ec0231fe1783954c7e52c60a3db. Complete installation dependencies and final production qualification remain open.
