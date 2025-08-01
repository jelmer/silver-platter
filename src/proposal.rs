//! Merge proposal related functions
use crate::vcs::{full_branch_url, open_branch};
use breezyshim::branch::{Branch, GenericBranch};
use breezyshim::error::Error as BrzError;
pub use breezyshim::forge::MergeProposal;
pub use breezyshim::forge::MergeProposalStatus;
use breezyshim::forge::{iter_forge_instances, Forge};
use serde::{Deserialize, Serialize};
use std::str::FromStr;
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
                Err(BrzError::ForgeLoginRequired) => {
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

/// Iterate over all merge proposals
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
        GenericBranch,
        String,
        GenericBranch,
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
                    full_branch_url(&resume_branch),
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

#[derive(Debug, Serialize, Deserialize, Clone, Hash, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
/// Description format for merge proposals descriptions
pub enum DescriptionFormat {
    /// Markdown format
    Markdown,

    /// HTML format
    Html,

    /// Plain text format
    Plain,
}

impl FromStr for DescriptionFormat {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "markdown" => Ok(DescriptionFormat::Markdown),
            "html" => Ok(DescriptionFormat::Html),
            "plain" => Ok(DescriptionFormat::Plain),
            _ => Err(format!("Unknown description format: {}", s)),
        }
    }
}

impl ToString for DescriptionFormat {
    fn to_string(&self) -> String {
        match self {
            DescriptionFormat::Markdown => "markdown".to_string(),
            DescriptionFormat::Html => "html".to_string(),
            DescriptionFormat::Plain => "plain".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_description_format_from_str() {
        assert_eq!(
            DescriptionFormat::from_str("markdown").unwrap(),
            DescriptionFormat::Markdown
        );
        assert_eq!(
            DescriptionFormat::from_str("html").unwrap(),
            DescriptionFormat::Html
        );
        assert_eq!(
            DescriptionFormat::from_str("plain").unwrap(),
            DescriptionFormat::Plain
        );

        // Test invalid format
        assert!(DescriptionFormat::from_str("invalid").is_err());
        let err = DescriptionFormat::from_str("invalid").unwrap_err();
        assert_eq!(err, "Unknown description format: invalid");
    }

    #[test]
    fn test_description_format_to_string() {
        assert_eq!(DescriptionFormat::Markdown.to_string(), "markdown");
        assert_eq!(DescriptionFormat::Html.to_string(), "html");
        assert_eq!(DescriptionFormat::Plain.to_string(), "plain");
    }

    #[test]
    fn test_description_format_serialization() {
        // Test serialization
        let format = DescriptionFormat::Markdown;
        let serialized = serde_json::to_string(&format).unwrap();
        assert_eq!(serialized, "\"markdown\"");

        // Test deserialization
        let deserialized: DescriptionFormat = serde_json::from_str("\"markdown\"").unwrap();
        assert_eq!(deserialized, DescriptionFormat::Markdown);

        let deserialized: DescriptionFormat = serde_json::from_str("\"html\"").unwrap();
        assert_eq!(deserialized, DescriptionFormat::Html);

        let deserialized: DescriptionFormat = serde_json::from_str("\"plain\"").unwrap();
        assert_eq!(deserialized, DescriptionFormat::Plain);
    }
}
