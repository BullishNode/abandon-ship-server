//! Postgres access. Reads captaind's tables; writes only the guarded claim,
//! the ban column, and the `sidecar` schema (see docs/deployment.md for the
//! least-privilege role).

use tokio_postgres::{Client, NoTls};

/// Advisory-lock key: only one sidecar instance may run against a database.
const LEADER_LOCK: i64 = 0x41_42_41_4e_44_4f_4e; // "ABANDON"

/// A row from captaind's `vtxo` table. Only `vtxo_id` and the encoded `vtxo`
/// blob are used for decisions; the blob is validated against the chain.
pub struct Candidate {
	pub vtxo_id: String,
	pub vtxo: Vec<u8>,
	/// DB amount: only used to skip uneconomic coins early, never to pay.
	pub db_amount: u64,
}

pub struct Payout {
	pub vtxo_id: String,
	pub amount_sat: u64,
	pub address: String,
	pub txid: Option<String>,
	pub raw_tx: Option<Vec<u8>>,
}

pub enum Claim {
	/// Coin flipped to spent and payout recorded.
	Claimed,
	/// The user redeemed it first (not spendable any more).
	Lost,
	/// The claim would break invariant I1 or captaind's row disagrees with
	/// the validated VTXO. Rolled back.
	Refused(String),
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

/// The sidecar does not create its tables: the DB admin runs
/// `migrations/0001_sidecar.sql` and owns them (no DELETE for the role).
pub async fn check_tables(db: &Client) -> anyhow::Result<()> {
	for t in ["sidecar.ban", "sidecar.quarantine", "sidecar.payout"] {
		let ok: bool = db.query_one("SELECT to_regclass($1) IS NOT NULL AS ok", &[&t]).await?.try_get("ok")?;
		anyhow::ensure!(ok, "table {t} missing: run migrations/0001_sidecar.sql as the DB admin");
	}
	Ok(())
}

/// Expired, unpaid, unquarantined, spendable user coins past the grace period,
/// of at least `min_amount` (filtered before the limit, so small coins never
/// fill the window). The `expiry` and `amount` columns are only pre-filters;
/// the validated VTXO decides.
pub async fn candidates(db: &Client, tip: u32, grace: u32, limit: i64, min_amount: u64) -> anyhow::Result<Vec<Candidate>> {
	let rows = db.query("
		SELECT v.vtxo_id, v.vtxo, v.amount
		FROM vtxo v
		WHERE v.policy_type = 'pubkey'
		  -- 'unclaimed' = a delegated refresh output whose owner never came back
		  AND v.spend_state IN ('spendable', 'unclaimed')
		  AND v.confirmed_height IS NULL
		  AND v.expiry::bigint + $1::bigint <= $2::bigint
		  AND v.amount >= $4::bigint
		  AND NOT EXISTS (SELECT 1 FROM sidecar.payout p WHERE p.vtxo_id = v.vtxo_id)
		  AND NOT EXISTS (SELECT 1 FROM sidecar.quarantine q WHERE q.vtxo_id = v.vtxo_id)
		ORDER BY v.expiry
		LIMIT $3::bigint * 20
	", &[&(grace as i64), &(tip as i64), &limit, &(min_amount as i64)]).await?;
	rows.into_iter()
		.map(|r| Ok(Candidate {
			vtxo_id: r.try_get("vtxo_id")?,
			vtxo: r.try_get("vtxo")?,
			db_amount: r.try_get::<_, i64>("amount")?.max(0) as u64,
		}))
		.collect()
}

/// The txid captaind recorded as spending an outpoint, if any. Only a hint:
/// the caller verifies it on-chain.
pub async fn recorded_spender(db: &Client, outpoint: &str) -> anyhow::Result<Option<String>> {
	let row = db.query_opt("SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id = $1", &[&outpoint]).await?;
	Ok(row.and_then(|r| r.get::<_, Option<String>>("onchain_spent_txid")))
}

/// Whether captaind has a vtxo created by this tx, i.e. it is a tree tx
/// (a partial unroll), not a sweep. Only used to choose between quarantine
/// and wait; never to decide a payout.
pub async fn is_tree_tx(db: &Client, txid: &str) -> anyhow::Result<bool> {
	Ok(db.query_one("SELECT EXISTS (SELECT 1 FROM vtxo WHERE vtxo_txid = $1) AS e", &[&txid])
		.await?.try_get::<_, bool>("e")?)
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

/// The atomic claim. In one transaction:
/// - flip the coin to spent iff still spendable (the race with the user);
/// - check captaind's amount equals the validated VTXO amount;
/// - enforce I1: payouts from this round never exceed its funding output;
/// - record the payout.
pub async fn claim(
	db: &mut Client, vtxo_id: &str, anchor_point: &str, amount_sat: u64, funding_value_sat: u64, address: &str,
	tip: u32,
) -> anyhow::Result<Claim> {
	let tx = db.transaction().await?;
	// Serialize claims per round so the I1 sum below is exact.
	tx.execute("SELECT pg_advisory_xact_lock(hashtext($1))", &[&anchor_point]).await?;

	let row = tx.query_opt("
		UPDATE vtxo SET spend_state = 'spent', updated_at = NOW()
		WHERE vtxo_id = $1 AND policy_type = 'pubkey'
		  AND spend_state IN ('spendable', 'unclaimed') AND confirmed_height IS NULL
		  -- our ban must still be exactly in place (not lifted by an operator)
		  AND banned_until_height > $2
		  AND banned_until_height = (SELECT until_height FROM sidecar.ban WHERE vtxo_id = $1)
		RETURNING amount
	", &[&vtxo_id, &(tip as i32)]).await?;
	let Some(row) = row else {
		tx.rollback().await?;
		return Ok(Claim::Lost);
	};
	let row_amount = row.try_get::<_, i64>("amount")?;
	if row_amount != amount_sat as i64 {
		tx.rollback().await?;
		return Ok(Claim::Refused(format!("db amount {row_amount} != validated {amount_sat}")));
	}
	let already: i64 = tx.query_one(
		"SELECT COALESCE(SUM(amount_sat), 0)::bigint AS s FROM sidecar.payout WHERE anchor_point = $1",
		&[&anchor_point],
	).await?.try_get("s")?;
	if already as u64 + amount_sat > funding_value_sat {
		tx.rollback().await?;
		return Ok(Claim::Refused(format!(
			"I1: round payouts {already} + {amount_sat} > funding output {funding_value_sat}",
		)));
	}
	tx.execute("
		INSERT INTO sidecar.payout (vtxo_id, anchor_point, amount_sat, address, state)
		VALUES ($1, $2, $3, $4, 'claimed')
	", &[&vtxo_id, &anchor_point, &(amount_sat as i64), &address]).await?;
	tx.commit().await?;
	Ok(Claim::Claimed)
}

pub async fn count_in_state(db: &Client, state: &str) -> anyhow::Result<i64> {
	Ok(db.query_one("SELECT COUNT(*) AS n FROM sidecar.payout WHERE state = $1", &[&state]).await?.try_get("n")?)
}

/// Every coin id in the ledger with a txid (for journal reconciliation).
pub async fn paid_ids(db: &Client) -> anyhow::Result<Vec<(String, String)>> {
	db.query("SELECT vtxo_id, txid FROM sidecar.payout WHERE txid IS NOT NULL", &[]).await?
		.into_iter().map(|r| Ok((r.try_get("vtxo_id")?, r.try_get("txid")?))).collect()
}

/// Journaled coins that are spendable/unclaimed again in captaind (DB restore).
pub async fn resurrected(db: &Client, ids: &[String]) -> anyhow::Result<Vec<String>> {
	Ok(db.query(
		"SELECT vtxo_id FROM vtxo WHERE vtxo_id = ANY($1) AND spend_state IN ('spendable','unclaimed')",
		&[&ids],
	).await?.into_iter().map(|r| Ok(r.try_get("vtxo_id")?)).collect::<anyhow::Result<_>>()?)
}

pub async fn payouts_in_state(db: &Client, state: &str) -> anyhow::Result<Vec<Payout>> {
	let rows = db.query(
		"SELECT vtxo_id, amount_sat, address, txid, raw_tx FROM sidecar.payout WHERE state = $1 ORDER BY claimed_at",
		&[&state],
	).await?;
	rows.into_iter().map(|r| Ok(Payout {
		vtxo_id: r.try_get("vtxo_id")?,
		amount_sat: r.try_get::<_, i64>("amount_sat")? as u64,
		address: r.try_get("address")?,
		txid: r.try_get("txid")?,
		raw_tx: r.try_get("raw_tx")?,
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

/// The hArk unlock preimage captaind holds for an unclaimed round output.
/// (captaind stores it as hex TEXT.)
pub async fn unlock_preimage(db: &Client, unlock_hash: &str) -> anyhow::Result<Option<Vec<u8>>> {
	let row = db.query_opt(
		"SELECT unlock_preimage FROM round_participation WHERE unlock_hash = $1", &[&unlock_hash],
	).await?;
	let Some(row) = row else { return Ok(None) };
	let Some(hex) = row.try_get::<_, Option<String>>("unlock_preimage")? else { return Ok(None) };
	Ok(Some(bitcoin::hex::FromHex::from_hex(&hex)?))
}

/// The encoded VTXO blob for a coin (to re-derive a payout from the chain).
pub async fn vtxo_blob(db: &Client, vtxo_id: &str) -> anyhow::Result<Option<Vec<u8>>> {
	let row = db.query_opt("SELECT vtxo FROM vtxo WHERE vtxo_id = $1", &[&vtxo_id]).await?;
	Ok(match row { Some(r) => Some(r.try_get("vtxo")?), None => None })
}

pub async fn raw_tx_by_txid(db: &Client, txid: &str) -> anyhow::Result<Option<Vec<u8>>> {
	let row = db.query_opt("SELECT raw_tx FROM sidecar.payout WHERE txid = $1 LIMIT 1", &[&txid]).await?;
	Ok(row.and_then(|r| r.get::<_, Option<Vec<u8>>>("raw_tx")))
}

pub async fn set_state_by_txid(db: &Client, txid: &str, from: &str, to: &str) -> anyhow::Result<()> {
	db.execute(
		"UPDATE sidecar.payout SET state = $3, updated_at = NOW() WHERE txid = $1 AND state = $2",
		&[&txid, &from, &to],
	).await?;
	Ok(())
}

/// I2: every paid coin is spent, with no round/arkoor/offboard spend recorded.
pub async fn check_invariants(db: &Client) -> anyhow::Result<()> {
	let bad: i64 = db.query_one("
		SELECT COUNT(*) AS n FROM sidecar.payout p
		JOIN vtxo v ON v.vtxo_id = p.vtxo_id
		WHERE v.spend_state <> 'spent'
		   OR v.spent_in_round IS NOT NULL
		   OR v.oor_spent_txid IS NOT NULL
		   OR v.offboarded_in IS NOT NULL
	", &[]).await?.try_get("n")?;
	anyhow::ensure!(bad == 0, "invariant I2 violated for {bad} payout row(s)");
	let orphans: i64 = db.query_one(
		"SELECT COUNT(*) AS n FROM sidecar.payout p WHERE NOT EXISTS (SELECT 1 FROM vtxo v WHERE v.vtxo_id = p.vtxo_id)",
		&[],
	).await?.try_get("n")?;
	anyhow::ensure!(orphans == 0, "invariant I2 violated: {orphans} paid coin row(s) deleted from vtxo");
	Ok(())
}
