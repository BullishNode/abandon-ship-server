-- Sidecar state, in captaind's Postgres under its own schema, so a claim
-- (the vtxo UPDATE) and its ledger row commit in one transaction.
-- The `sidecar` schema is created first (docs/deployment.md).

-- Coins banned by the sidecar while waiting for in-flight operations to clear.
CREATE TABLE IF NOT EXISTS sidecar.ban (
	vtxo_id       TEXT PRIMARY KEY,
	until_height  INTEGER NOT NULL,   -- the banned_until_height we wrote
	banned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Coins the sidecar will not touch automatically (undecodable, partial
-- unroll, already paid per the journal). Cleared only by a human.
CREATE TABLE IF NOT EXISTS sidecar.quarantine (
	vtxo_id    TEXT PRIMARY KEY,
	reason     TEXT NOT NULL,
	at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per paid coin. Never deleted; state only moves forward:
-- claimed -> signed -> broadcast -> confirmed.
CREATE TABLE IF NOT EXISTS sidecar.payout (
	vtxo_id       TEXT PRIMARY KEY,
	anchor_point  TEXT NOT NULL,      -- round funding outpoint
	amount_sat    BIGINT NOT NULL,    -- from the stored VTXO
	address       TEXT NOT NULL,      -- BIP86 tr(coin_pubkey)
	state         TEXT NOT NULL CHECK (state IN ('claimed','signed','broadcast','confirmed')),
	txid          TEXT,
	raw_tx        BYTEA,
	claimed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
	updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS payout_state_ix ON sidecar.payout (state);
