from typing import Sequence, Optional, Tuple

from breezy.branch import Branch
from breezy.controldir import Prober, ControlDirFormat
from breezy.forge import Forge, MergeProposal
from breezy.transport import Transport
from breezy.workingtree import WorkingTree

def full_branch_url(branch: Branch) -> str: ...

class Workspace:

    def __init__(self, main_branch: Optional[Branch] = None, resume_branch: Optional[Branch] = None, cached_branch: Optional[Branch] = None, dir: Optional[str] = None, path: Optional[str] = None, additional_colocated_branches: Optional[list[str] | dict[str, str]] = None, resume_branch_additional_colocated_branches: Optional[list[str] | dict[str, str]] = None, format: str | ControlDirFormat | None = None) -> None: ...

    @classmethod
    def from_url(cls, url: str) -> Workspace: ...

    path: str
    base_revid: bytes
    main_branch: Optional[Branch]
    main_branch_revid: Optional[bytes]
    resume_branch: Optional[Branch]
    local_tree: WorkingTree

    refreshed: bool

    def any_branch_changes(self) -> bool: ...

    def changes_since_base(self) -> bool: ...

    def changes_since_main(self) -> bool: ...

    def result_branches(self) -> Sequence[Tuple[str, Optional[bytes], Optional[bytes]]]: ...


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

def open_branch(url: str, possible_transports: Sequence[Transport] | None = None, probers: Sequence[Prober] | None = None, name: str | None = None) -> Branch: ...

def select_probers(vcs_type: Optional[str] = None) -> Sequence[Prober]: ...

def select_preferred_probers(vcs_type: Optional[str] = None) -> Sequence[Prober]: ...

def merge_conflicts(main_branch: Branch, other_branch: Branch, other_revision: Optional[bytes] = None) -> bool: ...

def find_existing_proposed(main_branch: Branch, forge: Forge, name: str, overwrite_unrelated: Optional[bool] = None, owner: Optional[str] = None, preferred_schemes: Optional[list[str]] = None) -> Tuple[Optional[Branch], Optional[bool], Optional[list[MergeProposal]]]: ...

class PublishResult:
    is_new: Optional[bool]
    forge: Optional[Forge]


def publish_changes(local_branch: Branch, main_branch: Branch, mode: str, name: str, get_proposal_description, resume_branch=None, get_proposal_commit_message=None, get_proposal_title=None, forge=None, allow_create_proposal=None, labels=None, overwrite_existing=None, existing_proposal=None, reviewers=None, tags=None, derived_owner=None, allow_collaboration=None, stop_revision=None) -> PublishResult: ...
