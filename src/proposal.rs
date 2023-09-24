use breezyshim::forge::{
    iter_forge_instances, Error as ForgeError, Forge, MergeProposal, MergeProposalStatus,
};

fn instance_iter_mps(
    instance: Forge,
    statuses: Option<Vec<MergeProposalStatus>>,
) -> impl Iterator<Item = MergeProposal> {
    let statuses = statuses.unwrap_or_else(|| vec![MergeProposalStatus::All]);
    statuses
        .into_iter()
        .flat_map(
            move |status| match instance.iter_my_proposals(Some(status), None) {
                Ok(mps) => Some(mps),
                Err(ForgeError::LoginRequired) => {
                    log::warn!("Skipping forge {:?} because login is required", instance);
                    None
                }
                Err(e) => {
                    log::error!("Error listing merge proposals: {:?}", e);
                    None
                }
            },
        )
        .flatten()
}

pub fn iter_all_mps(
    statuses: Option<Vec<MergeProposalStatus>>,
) -> impl Iterator<Item = (Forge, MergeProposal)> {
    let statuses = statuses.unwrap_or_else(|| vec![MergeProposalStatus::All]);
    iter_forge_instances().flat_map(move |instance| {
        instance_iter_mps(instance.clone(), Some(statuses.clone()))
            .map(move |mp| (instance.clone(), mp))
    })
}
