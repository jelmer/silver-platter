Command-line Interface Examples
===============================

This document provides examples of common Silver-Platter command-line usage.

Basic Commands
--------------

Running codemods on repositories::

    # Run a script on a single repository
    svp run https://github.com/user/repo ./update-deps.sh
    
    # Run with a specific name
    svp run --name=security-fix https://github.com/user/repo ./fix-vulnerability.sh
    
    # Run using a recipe file
    svp run --recipe=modernize.yaml https://github.com/user/repo

Authentication and Platform Management
--------------------------------------

Managing code hosting platforms::

    # List known hosting platforms
    svp hosters
    
    # Login to platforms
    svp login https://github.com/
    svp login https://gitlab.com/
    svp login https://salsa.debian.org/
    svp login https://launchpad.net/

Batch Operations
----------------

Working with multiple repositories::

    # Generate batch changes
    svp batch generate --recipe=fix-typo.yaml --candidates=repos.yaml my-batch
    
    # Review and publish batch changes
    svp batch publish my-batch
    
    # Check batch status
    svp batch status my-batch

Debian-Specific Commands
------------------------

Working with Debian packages::

    # Run a codemod on a Debian package (resolves from package name)
    debian-svp run samba ./update-standards.sh
    
    # Run lintian-brush fixes
    debian-svp run --recipe=lintian-brush.yaml samba
    
    # Different publishing modes
    debian-svp run --recipe=fix.yaml --mode=propose samba  # Create MR
    debian-svp run --recipe=fix.yaml --mode=push samba     # Push directly
    
    # Upload pending changes
    debian-svp upload-pending tdb
    
    # Import new upstream release
    debian-svp run --recipe=new-upstream-release.yaml --no-build-verify tdb
    
    # Find packages by maintainer
    debian-svp upload-pending --vcswatch --maintainer=jelmer@debian.org

Advanced Options
----------------

Common flags and options::

    # Resume from previous run
    svp run --resume https://github.com/user/repo ./long-running-script.sh
    
    # Show diff without publishing
    svp run --diff https://github.com/user/repo ./check-changes.sh
    
    # Set custom branch name
    svp run --branch=feature/api-v2 https://github.com/user/repo ./api-upgrade.sh
    
    # Add labels to merge requests
    svp run --label=security --label=automated https://github.com/user/repo ./fix.sh
    
    # Specify commit behavior
    svp run --commit-pending=auto https://github.com/user/repo ./make-changes.sh

Monorepo Support
----------------

Working with specific paths in monorepos::

    # Run on specific subdirectories
    svp run --paths=frontend,backend https://github.com/org/monorepo ./update.sh
    
    # Each path gets its own pull request
    # Results in: update-frontend and update-backend branches

See Also
--------

* Main README for installation and getting started
* Recipe examples in ``examples/`` directory
* Codemod protocol documentation in ``codemod-protocol.md``