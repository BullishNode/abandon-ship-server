//! Postgres access. Reads captaind's tables; writes only the guarded claim,
//! the ban column, and the `sidecar` schema.

use tokio_postgres::{Client, NoTls};

/// Advisory-lock key: only one sidecar instance may run against a database.
const LEADER_LOCK: i64 = 0x41_42_41_4e_44_4f_4e; // "ABANDON"

/// A row from captaind's `vtxo` table.
pub struct Candidate {
	pub expiry: i32,
	pub vtxo_id: String,
	pub vtxo: Vec<u8>,
	pub unclaimed: bool,
}

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

/// captaind's latest applied refinery migration version.
pub async fn captaind_schema_version(db: &Client) -> anyhow::Result<i32> {
	Ok(db.query_one("SELECT MAX(version) AS v FROM refinery_schema_history", &[]).await?.try_get("v")?)
}

/// The sidecar does not create its tables: `migrations/0001_sidecar.sql`
/// is applied once at setup (docs/deployment.md).
pub async fn check_tables(db: &Client) -> anyhow::Result<()> {
	for t in ["sidecar.ban", "sidecar.quarantine", "sidecar.payout", "sidecar.reassert"] {
		let ok: bool = db.query_one("SELECT to_regclass($1) IS NOT NULL AS ok", &[&t]).await?.try_get("ok")?;
		anyhow::ensure!(ok, "table {t} missing: apply migrations/0001_sidecar.sql");
	}
	Ok(())
}

/// Expired, unpaid, unquarantined, spendable user coins past the grace period,
/// of at least `min_amount` (filtered before the limit, so coins too small to
/// pay never fill the window).
pub async fn candidates(db: &Client, tip: u32, grace: u32, limit: i64, min_amount: u64, after: &(i32, String)) -> anyhow::Result<Vec<Candidate>> {
	let rows = db.query("
		SELECT v.vtxo_id, v.vtxo, v.expiry, v.spend_state = 'unclaimed' AS unclaimed
		FROM vtxo v
		WHERE v.policy_type = 'pubkey'
		  -- 'unclaimed' = a delegated refresh output whose owner never came back
		  AND v.spend_state IN ('spendable', 'unclaimed')
		  AND v.confirmed_height IS NULL
		  AND v.expiry::bigint + $1::bigint <= $2::bigint
		  AND v.amount >= $4::bigint
		  AND NOT EXISTS (SELECT 1 FROM sidecar.payout p WHERE p.vtxo_id = v.vtxo_id)
		  AND NOT EXISTS (SELECT 1 FROM sidecar.quarantine q WHERE q.vtxo_id = v.vtxo_id)
		  AND (v.expiry, v.vtxo_id) > ($5, $6)
		ORDER BY v.expiry, v.vtxo_id
		LIMIT $3::bigint * 20
	", &[&(grace as i64), &(tip as i64), &limit, &(min_amount as i64), &after.0, &after.1]).await?;
	rows.into_iter()
		.map(|r| Ok(Candidate {
			expiry: r.try_get("expiry")?,
			vtxo_id: r.try_get("vtxo_id")?,
			vtxo: r.try_get("vtxo")?,
			unclaimed: r.try_get("unclaimed")?,
		}))
		.collect()
}

/// Original inputs of this unclaimed hArk output's participation. Its own
/// sweep says nothing about an old input's still-usable unilateral exit.
pub async fn unclaimed_inputs(db: &Client, round_txid: &str, unlock_hash: &str) -> anyhow::Result<Vec<Option<Vec<u8>>>> {
	db.query("
		SELECT v.vtxo FROM round_participation p
		JOIN round_part_input i ON i.participation_id = p.id
		LEFT JOIN vtxo v ON v.vtxo_id = i.vtxo_id
		WHERE p.round_id = $1 AND p.unlock_hash = $2
	", &[&round_txid, &unlock_hash]).await?.into_iter()
		.map(|r| Ok(r.try_get("vtxo")?)).collect()
}

/// The txid captaind recorded as spending an outpoint, if any. Only a hint:
/// the caller verifies it on-chain.
pub async fn recorded_spender(db: &Client, outpoint: &str) -> anyhow::Result<Option<String>> {
	let row = db.query_opt("SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id = $1", &[&outpoint]).await?;
	Ok(row.and_then(|r| r.get::<_, Option<String>>("onchain_spent_txid")))
}

/// Any round participation still referencing the coin.
pub async fn in_round_participation(db: &Client, vtxo_id: &str) -> anyhow::Result<bool> {
	Ok(db.query_one("
		SELECT EXISTS (
			SELECT 1 FROM round_part_input i
			JOIN round_participation p ON p.id = i.participation_id
			WHERE i.vtxo_id = $1 AND p.forfeited_at IS NULL
		) AS e
	", &[&vtxo_id]).await?.try_get::<_, bool>("e")?)
}

/// Seconds since the sidecar's ban on this coin started, or None if the coin
/// is not under *our* ban: never banned, unbanned by an operator, re-banned
/// with another height, or lapsed. Any of those restarts the wait.
pub async fn ban_age_secs(db: &Client, vtxo_id: &str, tip: u32) -> anyhow::Result<Option<f64>> {
	let row = db.query_opt("
		SELECT EXTRACT(EPOCH FROM (NOW() - b.banned_at))::float8 AS age
		FROM sidecar.ban b JOIN vtxo v ON v.vtxo_id = b.vtxo_id
		WHERE b.vtxo_id = $1 AND v.banned_until_height = b.until_height AND b.until_height > $2
	", &[&vtxo_id, &(tip as i32)]).await?;
	Ok(match row { Some(r) => Some(r.try_get::<_, f64>("age")?), None => None })
}

/// Ban by writing captaind's own column, exactly as its admin `BanVtxo` does
/// (`server/src/database/ban.rs`). `until` must fit in i32 (captaind stores
/// it `as i32`; larger values wrap and break reads).
pub async fn ban(db: &mut Client, vtxo_id: &str, until: u32) -> anyhow::Result<()> {
	let until = i32::try_from(until).map_err(|_| anyhow::anyhow!("ban height {until} overflows i32"))?;
	let tx = db.transaction().await?;
	tx.execute("
		UPDATE vtxo SET banned_until_height = $2, updated_at = NOW()
		WHERE vtxo_id = $1 AND spend_state IN ('spendable', 'unclaimed')
		  -- never shorten a longer ban set by an operator
		  AND (banned_until_height IS NULL OR banned_until_height < $2)
	", &[&vtxo_id, &until]).await?;
	tx.execute("
		INSERT INTO sidecar.ban (vtxo_id, until_height) VALUES ($1, $2)
		ON CONFLICT (vtxo_id) DO UPDATE SET until_height = $2, banned_at = NOW()
	", &[&vtxo_id, &until]).await?;
	tx.commit().await?;
	Ok(())
}

/// Re-assert a journaled payout after a DB restore: flip the coin back to
/// spent (only if it is spendable), so captaind does not honour it in Ark too.
pub async fn reassert_paid(db: &Client, vtxo_id: &str) -> anyhow::Result<bool> {
	let n = db.execute("
		UPDATE vtxo SET spend_state = 'spent', updated_at = NOW()
		WHERE vtxo_id = $1 AND policy_type = 'pubkey' AND spend_state IN ('spendable', 'unclaimed')
	", &[&vtxo_id]).await?;
	Ok(n == 1)
}

pub async fn quarantine(db: &Client, vtxo_id: &str, reason: &str) -> anyhow::Result<()> {
	db.execute(
		"INSERT INTO sidecar.quarantine (vtxo_id, reason) VALUES ($1, $2) ON CONFLICT DO NOTHING",
		&[&vtxo_id, &reason],
	).await?;
	Ok(())
}

/// The atomic claim: flip the coin to spent iff it is still spendable and
/// our ban is intact (the race with the user), and record the payout, in one
/// transaction. False if the user redeemed it first.
pub async fn claim(
	db: &mut Client, vtxo_id: &str, anchor_point: &str, amount_sat: u64, address: &str, tip: u32,
) -> anyhow::Result<bool> {
	let tx = db.transaction().await?;
	let n = tx.execute("
		UPDATE vtxo SET spend_state = 'spent', updated_at = NOW()
		WHERE vtxo_id = $1 AND policy_type = 'pubkey'
		  AND spend_state IN ('spendable', 'unclaimed') AND confirmed_height IS NULL
		  -- our ban must still be exactly in place (not lifted by an operator)
		  AND banned_until_height > $2
		  AND banned_until_height = (SELECT until_height FROM sidecar.ban WHERE vtxo_id = $1)
	", &[&vtxo_id, &(tip as i32)]).await?;
	if n != 1 {
		tx.rollback().await?;
		return Ok(false);
	}
	tx.execute("
		INSERT INTO sidecar.payout (vtxo_id, anchor_point, amount_sat, address, state)
		VALUES ($1, $2, $3, $4, 'claimed')
	", &[&vtxo_id, &anchor_point, &(amount_sat as i64), &address]).await?;
	tx.commit().await?;
	Ok(true)
}

/// Every coin id in the ledger with its txid (for journal reconciliation).
/// Without the raw tx: every row of a batch stores the whole batch tx.
pub async fn paid_ids(db: &Client) -> anyhow::Result<Vec<(String, String, bool)>> {
	db.query("SELECT vtxo_id, txid, state = 'confirmed' AS confirmed FROM sidecar.payout WHERE txid IS NOT NULL", &[]).await?
		.into_iter().map(|r| Ok((r.try_get("vtxo_id")?, r.try_get("txid")?, r.try_get("confirmed")?))).collect()
}

pub async fn raw_tx(db: &Client, txid: &str) -> anyhow::Result<Vec<u8>> {
	Ok(db.query_one("SELECT raw_tx FROM sidecar.payout WHERE txid = $1 LIMIT 1", &[&txid]).await?.try_get("raw_tx")?)
}

/// Journaled coins that are spendable/unclaimed again in captaind (DB restore).
pub async fn resurrected(db: &Client, ids: &[String]) -> anyhow::Result<Vec<String>> {
	db.query(
		"SELECT vtxo_id FROM vtxo WHERE vtxo_id = ANY($1) AND spend_state IN ('spendable','unclaimed')",
		&[&ids],
	).await?.into_iter().map(|r| Ok(r.try_get("vtxo_id")?)).collect()
}

/// A payment journal cannot reconstruct Ark transfers missing from a backup.
pub async fn check_journal_history(db: &Client, ids: &[String]) -> anyhow::Result<()> {
	if let Some(row) = db.query_opt("
		SELECT j.id, v.vtxo_id IS NULL AS missing
		FROM unnest($1::text[]) AS j(id)
		LEFT JOIN vtxo v ON v.vtxo_id = j.id
		WHERE v.vtxo_id IS NULL OR v.policy_type <> 'pubkey'
		   OR v.spend_state NOT IN ('spendable', 'unclaimed', 'spent')
		   OR v.spent_in_round IS NOT NULL OR v.oor_spent_txid IS NOT NULL
		   OR v.offboarded_in IS NOT NULL OR v.confirmed_height IS NOT NULL
		   OR EXISTS (SELECT 1 FROM round_part_input i
		       JOIN round_participation p ON p.id = i.participation_id
		       WHERE i.vtxo_id = j.id AND p.forfeited_at IS NULL)
		LIMIT 1
	", &[&ids]).await? {
		let id: String = row.try_get("id")?;
		if row.try_get::<_, bool>("missing")? {
			anyhow::bail!("journaled coin {id} is missing from captaind history; restore a database backup and WAL containing its Ark history before restarting captaind");
		}
		anyhow::bail!("journaled coin {id} has conflicting Ark history or an unfinished round; restore the matching history or repair the round offline before restarting captaind");
	}
	Ok(())
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

/// Every paid coin is spent, with no round/arkoor/offboard spend recorded.
pub async fn check_invariants(db: &Client) -> anyhow::Result<()> {
	let bad: i64 = db.query_one("
		SELECT COUNT(*) AS n FROM sidecar.payout p
		JOIN vtxo v ON v.vtxo_id = p.vtxo_id
		WHERE v.spend_state <> 'spent'
		   OR v.spent_in_round IS NOT NULL
		   OR v.oor_spent_txid IS NOT NULL
		   OR v.offboarded_in IS NOT NULL
	", &[]).await?.try_get("n")?;
	anyhow::ensure!(bad == 0, "invariant violated for {bad} payout row(s)");
	let orphans: i64 = db.query_one(
		"SELECT COUNT(*) AS n FROM sidecar.payout p WHERE NOT EXISTS (SELECT 1 FROM vtxo v WHERE v.vtxo_id = p.vtxo_id)",
		&[],
	).await?.try_get("n")?;
	anyhow::ensure!(orphans == 0, "invariant violated: {orphans} paid coin row(s) deleted from vtxo");
	Ok(())
}

pub async fn receipt_amounts(db: &Client, txid: &str) -> anyhow::Result<Vec<(String, u64)>> {
	db.query("SELECT address, sum(amount_sat)::bigint AS amount FROM sidecar.payout
		WHERE txid = $1 GROUP BY address", &[&txid]).await?.into_iter()
		.map(|r| Ok((r.try_get("address")?, r.try_get::<_, i64>("amount")? as u64))).collect()
}
