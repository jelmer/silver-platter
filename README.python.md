# Python API for Silver-Platter

This document describes the Python API for Silver-Platter, which provides programmatic access to Silver-Platter's functionality for automating code changes across repositories.

## Installation

```bash
pip install silver-platter
```

## Overview

The Python API allows you to:
- Create workspaces for making changes to repositories
- Commit changes with appropriate metadata
- Publish changes as pull requests or direct pushes
- Handle Debian package repositories with specialized support

## Core Classes

### Workspace

The `Workspace` class is the primary interface for making changes to repositories. It exists in two forms:

* `silver_platter.Workspace` - For generic projects
* `silver_platter.debian.Workspace` - For Debian packages (with additional Debian-specific methods)

### Basic Usage

```python
from silver_platter import Workspace

# Create workspace from URL
with Workspace.from_url("https://github.com/user/repo") as ws:
    # Make changes in ws.path
    with open(os.path.join(ws.path, "README.md"), "a") as f:
        f.write("\n## New Section\n")
    
    # Commit if there are changes
    if ws.any_branch_changes():
        ws.commit(message="Add new section to README")
        ws.publish(mode="propose")
```

### Debian Package Example

```python
from silver_platter.debian import Workspace
import subprocess

with Workspace.from_apt_package(package="dulwich") as ws:
    # Make changes using Debian tools
    subprocess.check_call(['dch', 'Fix important bug'], cwd=ws.path)
    
    # Commit using debcommit-like behavior
    ws.commit()
    
    # Create merge proposal
    ws.publish(mode='propose')
```

## Key Methods

### Workspace Creation

- `Workspace.from_url(url)` - Create workspace from repository URL
- `Workspace.from_apt_package(package)` - Create workspace for Debian package (Debian workspace only)

### Change Detection

- `ws.any_branch_changes()` - Check if there are any uncommitted changes
- `ws.changes_since_base()` - Check for changes since the base revision
- `ws.changes_since_main()` - Check for changes since the main branch

### Publishing Changes

- `ws.publish(mode, ...)` - Publish changes
  - `mode='propose'` - Create a merge/pull request
  - `mode='push'` - Push directly to the branch
  - `mode='attempt-push'` - Try pushing, fall back to propose if needed

## Advanced Features

### Working with Resume Branches

When creating a workspace, you can resume from a previous branch:

```python
with Workspace(
    main_branch=main,
    resume_branch=previous_branch,
    cached_branch=cache
) as ws:
    # Continue work from previous branch
    ...
```

### Custom Merge Proposals

```python
def get_description():
    return "This PR fixes issue #123\n\nDetailed description..."

def get_title():
    return "Fix: Resolve critical bug in parser"

result = publish_changes(
    local_branch=ws.local_tree.branch,
    main_branch=ws.main_branch,
    mode="propose",
    name="fix-parser-bug",
    get_proposal_description=get_description,
    get_proposal_title=get_title,
    labels=["bug", "critical"],
    reviewers=["reviewer1", "reviewer2"]
)
```

## Error Handling

```python
from silver_platter import (
    EmptyMergeProposal,
    InsufficientChangesForNewProposal
)

try:
    ws.publish(mode="propose")
except EmptyMergeProposal:
    print("No changes to propose")
except InsufficientChangesForNewProposal:
    print("Changes too small for a new proposal")
```

## Integration with Codemods

For running codemods that follow the Silver-Platter protocol, see the [Codemod Protocol](codemod-protocol.md) documentation.

## Complete Example

```python
from silver_platter import Workspace
import os
import subprocess

def update_dependencies(repo_url):
    """Update dependencies in a repository."""
    with Workspace.from_url(repo_url) as ws:
        # Run dependency update tool
        subprocess.run(
            ["npm", "update"],
            cwd=ws.path,
            check=True
        )
        
        # Check if package-lock.json changed
        if ws.any_branch_changes():
            # Commit the changes
            ws.commit(message="Update npm dependencies")
            
            # Create pull request
            result = ws.publish(
                mode="propose",
                name="update-dependencies",
                description="Automated dependency update",
                labels=["dependencies", "automated"]
            )
            
            print(f"Created PR: {result.proposal.url}")
        else:
            print("No dependency updates needed")

# Use it
update_dependencies("https://github.com/user/my-project")
```

## See Also

- [Main Documentation](README.md) - Overview and CLI usage
- [Codemod Protocol](codemod-protocol.md) - Writing compatible codemods
- [API Reference](https://pypi.org/project/silver-platter/) - Full API documentation on PyPI
