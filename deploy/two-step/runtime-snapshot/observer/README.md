# Read-only observer dependency repair

These are the exact source bytes installed for the existing Track A observer. They preserve the original transaction projection, identity checks and secret filter. A missing public BIP39 dictionary previously caused the first nonempty recovery receipt to fail with FileNotFoundError. The repair adds that unchanged dictionary and its source hash to the existing pinned dependency list.

`observe.py` records this particular deployment's source identities and paths. It is a deployment snapshot, not a generic service installer. The five `simulator` files belong together at its declared source path. Private credentials, configuration bodies, state and SQL snapshots are excluded.

The original failure was reproduced with the actual confirmed sweep. The repaired observer and the independent simulator-side restricted bridge both exported its stripped body and signed weight successfully, with no witness export. Running financial daemons, configurations, images and process identities stayed unchanged; no payment or campaign dispatch occurred. This validates the observer projection only and does not establish customer payment or production readiness.
