//! The single captaind admin RPC used for ledger handoff and sweep hints.

use std::time::Duration;
use anyhow::Context;
use ark::VtxoId;
use bitcoin::OutPoint;
use server_rpc::admin::ExpirySettlementAdminServiceClient;
use server_rpc::protos::{self, expiry_settlement_request::Operation};
use server_rpc::tonic::transport::{Channel, Endpoint};

pub use protos::expiry_settlement_claim_result::Status;

pub struct Candidate {
	pub vtxo_id: String,
	pub vtxo: Vec<u8>,
	pub expiry: u32,
}

impl TryFrom<protos::ExpirySettlementVtxo> for Candidate {
	type Error = anyhow::Error;
	fn try_from(v: protos::ExpirySettlementVtxo) -> anyhow::Result<Self> {
		Ok(Self { vtxo_id: VtxoId::from_slice(&v.vtxo_id)?.to_string(), vtxo: v.vtxo, expiry: v.expiry })
	}
}

pub struct Captaind(ExpirySettlementAdminServiceClient<Channel>);

impl Captaind {
	pub fn new(url: &str) -> anyhow::Result<Self> {
		let channel = Endpoint::from_shared(url.to_owned())?
			.connect_timeout(Duration::from_secs(5)).timeout(Duration::from_secs(10)).connect_lazy();
		Ok(Self(ExpirySettlementAdminServiceClient::new(channel)))
	}

	async fn exchange(&mut self, operation: Operation) -> anyhow::Result<protos::ExpirySettlementResponse> {
		Ok(self.0.exchange(protos::ExpirySettlementRequest { operation: Some(operation) }).await?.into_inner())
	}

	pub async fn page(
		&mut self, claimed: bool, grace: u32, minimum: u64, after: &(u32, String), limit: u32,
	) -> anyhow::Result<Vec<Candidate>> {
		let after_vtxo_id = if after.1.is_empty() { Vec::new() }
			else { after.1.parse::<VtxoId>()?.to_bytes().to_vec() };
		self.exchange(Operation::Page(protos::ExpirySettlementPage {
			claimed, grace_blocks: grace, min_amount_sat: minimum,
			after_expiry: after.0, after_vtxo_id, limit,
		})).await?.vtxos.into_iter().map(TryInto::try_into).collect()
	}

	pub async fn claim(&mut self, id: &str, grace: u32) -> anyhow::Result<(Status, Option<Candidate>)> {
		let result = self.exchange(Operation::Claim(protos::ExpirySettlementClaim {
			vtxo_ids: vec![id.parse::<VtxoId>()?.to_bytes().to_vec()], grace_blocks: grace,
		})).await?.claims.into_iter().next().context("missing claim result")?;
		Ok((Status::try_from(result.status)?, result.vtxo.map(TryInto::try_into).transpose()?))
	}

	pub async fn spenders(&mut self, outpoints: &[OutPoint]) -> anyhow::Result<Vec<Option<String>>> {
		let mut ret = Vec::with_capacity(outpoints.len());
		for batch in outpoints.chunks(256) {
			let ids = batch.iter().map(|o| Ok(o.to_string().parse::<VtxoId>()?.to_bytes().to_vec()))
				.collect::<anyhow::Result<Vec<_>>>()?;
			let response = self.exchange(Operation::Spenders(protos::ExpirySettlementSpenders { outpoints: ids })).await?;
			anyhow::ensure!(response.spenders.len() == batch.len(), "incomplete spender hints");
			ret.extend(response.spenders.into_iter().map(|s| s.txid));
		}
		Ok(ret)
	}
}
