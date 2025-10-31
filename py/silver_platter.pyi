from collections.abc import Sequence

from breezy.branch import Branch
from breezy.controldir import ControlDirFormat, Prober
from breezy.forge import Forge, MergeProposal
from breezy.workingtree import WorkingTree

def full_branch_url(branch: Branch) -> str:
    """Return the full URL of the branch.

    This includes the branch name.
    """
    ...

class Workspace:
    """Workspace for creating changes to a branch.

    Args:
      main_branch: The upstream branch
      resume_branch: Optional in-progress branch that we previously made
        changes on, and should ideally continue from.
      resume_branch_additional_colocated_branches: Additional list of colocated branches to fetch
      cached_branch: Branch to copy revisions from, if possible.
      local_tree: The tree the user can work in
    """

    def __init__(
        self,
        main_branch: Branch | None = None,
        *,
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
    ) -> None:
        """Create a new workspace.

        Args:
          main_branch: The upstream branch (if any)
          resume_branch: Optional in-progress branch that we previously made
            changes on, and should ideally continue from.
          cached_branch: Branch to copy revisions from, if possible.
          dir: Directory to create the workspace in.
          path: Path to the workspace.
          additional_colocated_branches: Additional colocated branches to fetch
          resume_branch_additional_colocated_branches: Additional colocated branches to fetch
          format: Format of the workspace. If None, the default format is used.
        """
        ...

    @classmethod
    def from_url(cls, url: str) -> Workspace:
        """Create a new workspace from a URL.

        Args:
          url: URL of the branch to create the workspace from.

        Returns:
          A new workspace.
        """
        ...

    path: str
    """Path to the workspace."""

    base_revid: bytes
    """Revision ID of the base revision."""

    main_branch: Branch | None
    """The upstream branch."""

    main_branch_revid: bytes | None
    """Revision ID of the upstream branch."""

    resume_branch: Branch | None
    """The branch we are resuming from."""

    local_tree: WorkingTree
    """The working tree we are using."""

    refreshed: bool
    """Whether the workspace has been refreshed."""

    def any_branch_changes(self) -> bool:
        """Check if there are any changes in the branch."""
        ...

    def changes_since_base(self) -> bool:
        """Check if there are any changes since the base revision."""
        ...

    def changes_since_main(self) -> bool:
        """Check if there are any changes since the main branch."""
        ...

    def result_branches(
        self,
    ) -> Sequence[tuple[str, bytes | None, bytes | None]]:
        """Return the result branches.

        Returns:
          list of tuples of (branch name, revision ID, base revision ID)
        """
        ...

class EmptyMergeProposal(Exception):
    """Raised when a merge proposal is empty."""

class InsufficientChangesForNewProposal(Exception):
    """Raised when there are insufficient changes for a new proposal."""

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
