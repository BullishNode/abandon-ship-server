-- Apply to the sidecar's independent state database.
CREATE SCHEMA IF NOT EXISTS sidecar;

-- Validation failures retained for manual release.
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
