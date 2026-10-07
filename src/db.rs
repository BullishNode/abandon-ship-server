//! The sidecar's independent ledger. Captaind state is accessed through its RPC.

use std::collections::HashSet;
use tokio_postgres::{Client, NoTls};

const LEADER_LOCK: i64 = 0x41_42_41_4e_44_4f_4e;

pub struct Payout {
	pub vtxo_id: String,
	pub amount_sat: u64,
	pub address: String,
}

pub async fn connect(conninfo: &str) -> anyhow::Result<Client> {
	let (client, conn) = tokio_postgres::connect(conninfo, NoTls).await?;
	tokio::spawn(async move {
		if let Err(e) = conn.await {
			tracing::error!("postgres connection error: {e}");
		}
	});
	Ok(client)
}

/// Take the leader lock for this session; false if another instance holds it.
pub async fn try_lead(db: &Client) -> anyhow::Result<bool> {
	Ok(db.query_one("SELECT pg_try_advisory_lock($1) AS ok", &[&LEADER_LOCK]).await?.try_get("ok")?)
}

pub async fn check_tables(db: &Client) -> anyhow::Result<()> {
	for t in ["sidecar.quarantine", "sidecar.payout"] {
		let ok: bool = db.query_one("SELECT to_regclass($1) IS NOT NULL AS ok", &[&t]).await?.try_get("ok")?;
		anyhow::ensure!(ok, "table {t} missing: apply migrations/0001_sidecar.sql");
	}
	Ok(())
}

pub async fn quarantine(db: &Client, vtxo_id: &str, reason: &str) -> anyhow::Result<()> {
	db.execute(
		"INSERT INTO sidecar.quarantine (vtxo_id, reason) VALUES ($1, $2) ON CONFLICT DO NOTHING",
		&[&vtxo_id, &reason],
	).await?;
	Ok(())
}

/// Store an already committed captaind handoff. A failed insert is recovered
/// from captaind's permanent receipt; an existing local row keeps its state.
pub async fn store_claim(
	db: &Client, id: &str, anchor: &str, amount: u64, address: &str,
) -> anyhow::Result<()> {
	db.execute("INSERT INTO sidecar.payout (vtxo_id, anchor_point, amount_sat, address, state)
		VALUES ($1, $2, $3, $4, 'claimed') ON CONFLICT DO NOTHING",
		&[&id, &anchor, &(amount as i64), &address]).await?;
	Ok(())
}

pub async fn payout_ids(db: &Client) -> anyhow::Result<HashSet<String>> {
	Ok(db.query("SELECT vtxo_id FROM sidecar.payout", &[]).await?
		.into_iter().map(|r| r.get(0)).collect())
}

pub async fn excluded(db: &Client, ids: &[String]) -> anyhow::Result<HashSet<String>> {
	Ok(db.query("SELECT vtxo_id FROM sidecar.payout WHERE vtxo_id = ANY($1)
		UNION SELECT vtxo_id FROM sidecar.quarantine WHERE vtxo_id = ANY($1)", &[&ids]).await?
		.into_iter().map(|r| r.get(0)).collect())
}

/// Every coin id in the ledger with its txid (for journal reconciliation).
/// Without the raw tx: every row of a batch stores the whole batch tx.
pub async fn paid_ids(db: &Client) -> anyhow::Result<Vec<(String, String, bool)>> {
	db.query("SELECT vtxo_id, txid, state = 'confirmed' AS confirmed FROM sidecar.payout WHERE txid IS NOT NULL", &[]).await?
		.into_iter().map(|r| Ok((r.try_get("vtxo_id")?, r.try_get("txid")?, r.try_get("confirmed")?))).collect()
}

pub async fn raw_tx(db: &Client, txid: &str) -> anyhow::Result<Option<Vec<u8>>> {
	db.query_opt("SELECT raw_tx FROM sidecar.payout WHERE txid = $1 AND raw_tx IS NOT NULL LIMIT 1", &[&txid])
		.await?.map(|row| Ok(row.try_get("raw_tx")?)).transpose()
}

pub async fn receipt_amounts(db: &Client, txid: &str) -> anyhow::Result<Vec<(String, u64)>> {
	db.query("SELECT address, sum(amount_sat)::bigint AS amount FROM sidecar.payout
		WHERE txid = $1 GROUP BY address", &[&txid]).await?.into_iter()
		.map(|r| Ok((r.try_get("address")?, r.try_get::<_, i64>("amount")? as u64))).collect()
}

pub async fn claimed_payouts(db: &Client) -> anyhow::Result<Vec<Payout>> {
	let rows = db.query(
		"SELECT vtxo_id, amount_sat, address FROM sidecar.payout WHERE state = 'claimed' ORDER BY claimed_at",
		&[],
	).await?;
	rows.into_iter().map(|r| Ok(Payout {
		vtxo_id: r.try_get("vtxo_id")?,
		amount_sat: r.try_get::<_, i64>("amount_sat")? as u64,
		address: r.try_get("address")?,
	})).collect()
}

/// Store the signed batch tx for these claims before it is broadcast.
pub async fn mark_signed(db: &mut Client, vtxo_ids: &[String], txid: &str, raw: &[u8]) -> anyhow::Result<()> {
	let tx = db.transaction().await?;
	let n = tx.execute("
		UPDATE sidecar.payout SET state = 'signed', txid = $2, raw_tx = $3, updated_at = NOW()
		WHERE vtxo_id = ANY($1) AND state = 'claimed'
	", &[&vtxo_ids, &txid, &raw]).await?;
	anyhow::ensure!(n as usize == vtxo_ids.len(), "claimed rows changed under us");
	tx.commit().await?;
	Ok(())
}

pub async fn set_state_by_txid(db: &Client, txid: &str, to: &str) -> anyhow::Result<()> {
	db.execute(
		"UPDATE sidecar.payout SET state = $2, updated_at = NOW() WHERE txid = $1 AND state IN ('signed','broadcast')",
		&[&txid, &to],
	).await?;
	Ok(())
}
