//! Run the given command and publish the changes as a merge proposal.
use crate::debian::codemod::{CommandResult, Error as CommandError};
use crate::publish::{
    enable_tag_pushing, find_existing_proposed, DescriptionFormat, Error as PublishError,
};
use crate::vcs::{open_branch, BranchOpenError};
use crate::workspace::Workspace;
use crate::Mode;
use breezyshim::branch::Branch;
use breezyshim::error::Error as BrzError;
use breezyshim::forge::{get_forge, Forge, MergeProposal};
use log::{error, info, warn};
use std::collections::HashMap;
use url::Url;

/// Run the given command and publish the changes as a merge proposal.
pub fn apply_and_publish(
    url: &Url,
    name: &str,
    command: &[&str],
    mode: Mode,
    commit_pending: crate::CommitPending,
    labels: Option<&[&str]>,
    diff: bool,
    derived_owner: Option<&str>,
    refresh: bool,
    allow_create_proposal: Option<impl FnOnce(&CommandResult) -> bool>,
    mut get_commit_message: Option<
        impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>,
    >,
    get_title: Option<impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>>,
    get_description: impl FnOnce(&CommandResult, DescriptionFormat, Option<&MergeProposal>) -> String,
    update_changelog: Option<bool>,
    build_verify: bool,
    mut build_target_dir: Option<std::path::PathBuf>,
    builder: Option<String>,
    install: bool,
    extra_env: Option<HashMap<String, String>>,
) -> i32 {
    let main_branch = match open_branch(url, None, None, None) {
        Err(BranchOpenError::Unavailable {
            url, description, ..
        })
        | Err(BranchOpenError::Missing {
            url, description, ..
        })
        | Err(BranchOpenError::RateLimited {
            url, description, ..
        })
        | Err(BranchOpenError::TemporarilyUnavailable {
            url, description, ..
        })
        | Err(BranchOpenError::Unsupported {
            url, description, ..
        }) => {
            error!("{}: {}", url, description);
            return 2;
        }
        Err(BranchOpenError::Other(e)) => {
            error!("{}: {}", url, e);
            return 2;
        }
        Ok(b) => b,
    };

    let mut overwrite = false;

    let (forge, existing_proposals, mut resume_branch): (
        Option<Forge>,
        Vec<MergeProposal>,
        Option<Box<dyn Branch>>,
    ) = match get_forge(main_branch.as_ref()) {
        Err(BrzError::UnsupportedForge(e)) => {
            if mode != Mode::Push {
                error!("{}: {}", url, e);
                return 2;
            }
            // We can't figure out what branch to resume from when there's no forge
            // that can tell us.
            warn!(
                "Unsupported forge ({}), will attempt to push to {}",
                e,
                crate::vcs::full_branch_url(main_branch.as_ref()),
            );
            (None, vec![], None)
        }
        Err(BrzError::ForgeProjectExists(_)) | Err(BrzError::AlreadyControlDir(..)) => {
            unreachable!()
        }
        Err(BrzError::ForgeLoginRequired) => {
            warn!("Login required to access forge");
            return 2;
        }
        Err(e) => {
            error!("Failed to get forge: {}", e);
            return 2;
        }
        Ok(ref forge) => {
            let (resume_branch, resume_overwrite, existing_proposals) = find_existing_proposed(
                main_branch.as_ref(),
                forge,
                name,
                false,
                derived_owner,
                None,
            )
            .unwrap();
            if let Some(resume_overwrite) = resume_overwrite {
                overwrite = resume_overwrite;
            }
            (
                Some(forge.clone()),
                existing_proposals.unwrap_or_default(),
                resume_branch,
            )
        }
    };

    if refresh {
        if resume_branch.is_some() {
            overwrite = true;
        }
        resume_branch = None;
    }

    let existing_proposal = if existing_proposals.len() > 1 {
        warn!(
            "Multiple open merge proposals for branch at {}: {:?}",
            resume_branch.as_ref().unwrap().get_user_url(),
            existing_proposals
                .iter()
                .map(|mp| mp.url().unwrap())
                .collect::<Vec<_>>()
        );
        let existing_proposal = existing_proposals.into_iter().next().unwrap();
        info!("Updating {}", existing_proposal.url().unwrap());
        Some(existing_proposal)
    } else {
        None
    };

    let subpath = std::path::Path::new("");

    let mut ws_builder = Workspace::builder();

    ws_builder = ws_builder.additional_colocated_branches(
        crate::debian::pick_additional_colocated_branches(main_branch.as_ref()),
    );

    ws_builder = ws_builder.main_branch(main_branch);

    ws_builder = if let Some(resume_branch) = resume_branch.take() {
        ws_builder.resume_branch(resume_branch)
    } else {
        ws_builder
    };

    let ws = match ws_builder.build() {
        Ok(ws) => ws,
        Err(e) => {
            error!("Failed to start workspace: {}", e);
            return 2;
        }
    };

    let result: CommandResult = match crate::debian::codemod::script_runner(
        ws.local_tree(),
        command,
        subpath,
        commit_pending,
        None,
        None,
        extra_env,
        std::process::Stdio::inherit(),
        update_changelog,
    ) {
        Ok(r) => r,
        Err(CommandError::ScriptMadeNoChanges) => {
            error!("Script did not make any changes.");
            return 0;
        }
        Err(e) => {
            error!("Script failed: {}", e);
            return 2;
        }
    };

    let mut td = None;

    if build_verify {
        if build_target_dir.is_none() {
            td = Some(tempfile::tempdir().unwrap());
            build_target_dir = td.as_ref().map(|td| td.path().to_path_buf());
        }

        crate::debian::build(
            ws.local_tree(),
            subpath,
            builder.as_deref(),
            build_target_dir.as_deref(),
        )
        .unwrap();
    }

    enable_tag_pushing(ws.local_tree().branch().as_ref()).unwrap();

    let result_ref = result.clone();
    let get_commit_message = get_commit_message
        .take()
        .map(|f| move |ep: Option<&MergeProposal>| -> Option<String> { f(&result_ref, ep) });

    let result_ref = result.clone();

    let publish_result = match ws.publish_changes(
        None,
        mode,
        name,
        |df, ep| get_description(&result, df, ep),
        get_commit_message,
        Some(move |ep: Option<&MergeProposal>| {
            if let Some(get_title) = get_title {
                get_title(&result_ref, ep)
            } else {
                None
            }
        }),
        forge.as_ref(),
        allow_create_proposal.map(|f| f(&result)),
        labels.map(|l| l.iter().map(|s| s.to_string()).collect()),
        Some(overwrite),
        existing_proposal,
        None,
        None,
        derived_owner,
        None,
        None,
        None,
    ) {
        Ok(r) => r,
        Err(PublishError::UnsupportedForge(_)) => {
            error!(
                "No known supported forge for {}. Run 'svp login'?",
                crate::vcs::full_branch_url(ws.main_branch().unwrap()),
            );
            return 2;
        }
        Err(PublishError::InsufficientChangesForNewProposal) => {
            info!("Insufficient changes for a new merge proposal");
            return 1;
        }
        Err(PublishError::ForgeLoginRequired) => {
            error!("Credentials for hosting site missing. Run 'svp login'?",);
            return 2;
        }
        Err(PublishError::DivergedBranches()) | Err(PublishError::UnrelatedBranchExists) => {
            error!("A branch exists on the server that has diverged from the local branch.");
            return 2;
        }
        Err(PublishError::BranchOpenError(e)) => {
            error!("Failed to open branch: {}", e);
            return 2;
        }
        Err(PublishError::EmptyMergeProposal) => {
            error!("No changes to publish.");
            return 2;
        }
        Err(PublishError::PermissionDenied) => {
            error!("Permission denied to create merge proposal.");
            return 2;
        }
        Err(PublishError::Other(e)) => {
            error!("Failed to publish changes: {}", e);
            return 2;
        }
        Err(PublishError::NoTargetBranch) => {
            unreachable!()
        }
    };

    if let Some(mp) = publish_result.proposal {
        if publish_result.is_new.unwrap() {
            info!("Merge proposal created.");
        } else {
            info!("Merge proposal updated.");
        }
        if let Ok(url) = mp.url() {
            info!("URL: {}", url);
        }
        info!("Description: {}", mp.get_description().unwrap().unwrap());
    }

    if diff {
        ws.show_diff(Box::new(std::io::stdout()), None, None)
            .unwrap();
    }

    if install {
        crate::debian::install_built_package(
            ws.local_tree(),
            subpath,
            build_target_dir.as_ref().unwrap(),
        )
        .unwrap();
    }

    if let Some(td) = td.take() {
        td.close().unwrap();
    }

    1
}
