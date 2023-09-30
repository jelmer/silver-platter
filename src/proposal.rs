use crate::vcs::{full_branch_url, open_branch};
use breezyshim::branch::Branch;
use breezyshim::forge::{
    iter_forge_instances, Error as ForgeError, Forge, MergeProposal, MergeProposalStatus,
};
use url::Url;

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

/// Find conflicted branches owned by the current user.
///
/// # Arguments
/// * `branch_name`: Branch name to search for
pub fn iter_conflicted(
    branch_name: &str,
) -> impl Iterator<
    Item = (
        Url,
        Box<dyn Branch + '_>,
        String,
        Box<dyn Branch + '_>,
        Forge,
        MergeProposal,
        bool,
    ),
> + '_ {
    let mut possible_transports = vec![];

    iter_all_mps(Some(vec![MergeProposalStatus::Open])).filter_map(move |(forge, mp)| {
        if mp.can_be_merged().unwrap() {
            None
        } else {
            let main_branch = open_branch(
                &mp.get_target_branch_url().unwrap().unwrap(),
                Some(&mut possible_transports),
                None,
                None,
            )
            .unwrap();
            let resume_branch = open_branch(
                &mp.get_source_branch_url().unwrap().unwrap(),
                Some(&mut possible_transports),
                None,
                None,
            )
            .unwrap();
            if resume_branch.name().as_deref() != Some(branch_name)
                && !(resume_branch.name().is_none()
                    && resume_branch.get_user_url().as_str().ends_with(branch_name))
            {
                None
            } else {
                // TODO(jelmer): Find out somehow whether we need to modify a subpath?
                let subpath = "";

                Some((
                    full_branch_url(resume_branch.as_ref()),
                    main_branch,
                    subpath.to_string(),
                    resume_branch,
                    forge,
                    mp,
                    true,
                ))
            }
        }
    })
}
