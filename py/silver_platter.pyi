from typing import Sequence

from breezy.branch import Branch
from breezy.controldir import ControlDirFormat, Prober
from breezy.forge import Forge, MergeProposal
from breezy.transport import Transport
from breezy.workingtree import WorkingTree

def full_branch_url(branch: Branch) -> str: ...

class Workspace:
    def __init__(
        self,
        main_branch: Branch | None = None,
        resume_branch: Branch | None = None,
        cached_branch: Branch | None = None,
        dir: str | None = None,
        path: str | None = None,
        additional_colocated_branches: list[str]
        | dict[str, str]
        | None = None,
        resume_branch_additional_colocated_branches: list[str]
        | dict[str, str]
        | None = None,
        format: str | ControlDirFormat | None = None,
    ) -> None: ...
    @classmethod
    def from_url(cls, url: str) -> Workspace: ...

    path: str
    base_revid: bytes
    main_branch: Branch | None
    main_branch_revid: bytes | None
    resume_branch: Branch | None
    local_tree: WorkingTree

    refreshed: bool

    def any_branch_changes(self) -> bool: ...
    def changes_since_base(self) -> bool: ...
    def changes_since_main(self) -> bool: ...
    def result_branches(
        self,
    ) -> Sequence[tuple[str, bytes | None, bytes | None]]: ...

class BranchMissing(Exception):
    """Raised when a branch is missing."""

class BranchRateLimited(Exception):
    """Raised when a branch is rate limited."""

class BranchTemporarilyUnavailable(Exception):
    """Raised when a branch is temporarily unavailable."""

class BranchUnavailable(Exception):
    """Raised when a branch is unavailable."""

class BranchUnsupported(Exception):
    """Raised when a branch is unsupported."""

class EmptyMergeProposal(Exception):
    """Raised when a merge proposal is empty."""

class InsufficientChangesForNewProposal(Exception):
    """Raised when there are insufficient changes for a new proposal."""

def open_branch(
    url: str,
    possible_transports: Sequence[Transport] | None = None,
    probers: Sequence[Prober] | None = None,
    name: str | None = None,
) -> Branch: ...
def select_probers(vcs_type: str | None = None) -> Sequence[Prober]: ...
def select_preferred_probers(
    vcs_type: str | None = None,
) -> Sequence[Prober]: ...
def merge_conflicts(
    main_branch: Branch,
    other_branch: Branch,
    other_revision: bytes | None = None,
) -> bool: ...
def find_existing_proposed(
    main_branch: Branch,
    forge: Forge,
    name: str,
    overwrite_unrelated: bool | None = None,
    owner: str | None = None,
    preferred_schemes: list[str] | None = None,
) -> tuple[Branch | None, bool | None, list[MergeProposal] | None]: ...

class PublishResult:
    is_new: bool | None
    forge: Forge | None

def publish_changes(
    local_branch: Branch,
    main_branch: Branch,
    mode: str,
    name: str,
    get_proposal_description,
    resume_branch=None,
    get_proposal_commit_message=None,
    get_proposal_title=None,
    forge=None,
    allow_create_proposal=None,
    labels=None,
    overwrite_existing=None,
    existing_proposal=None,
    reviewers=None,
    tags=None,
    derived_owner=None,
    allow_collaboration=None,
    stop_revision=None,
) -> PublishResult: ...
