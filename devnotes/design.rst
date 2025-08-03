Silver-Platter Design Notes
===========================

This document contains design notes and architectural decisions for Silver-Platter.

Architecture Overview
---------------------

Silver-Platter is built as a Rust application with Python bindings, designed to:

1. **Workspace Management** - Create isolated working directories for changes
2. **VCS Abstraction** - Support multiple version control systems (Git, Bazaar)
3. **Forge Integration** - Work with GitHub, GitLab, Launchpad
4. **Codemod Execution** - Run arbitrary scripts in a controlled environment
5. **Change Publishing** - Create pull requests or push directly

Core Components
---------------

**Workspace (src/workspace.rs)**
  Manages the lifecycle of a working directory where changes are made.
  Handles cloning, branching, and cleanup.

**Codemod Runner (src/codemod.rs)**
  Executes user-provided scripts with the proper environment variables
  and captures their results.

**Publisher (src/publish.rs)**  
  Handles the creation and updating of merge proposals across different
  platforms.

**Recipe System (src/recipe.rs)**
  Parses YAML recipe files that define reusable codemod patterns.

Design Principles
-----------------

1. **Platform Agnostic** - Abstract differences between Git/Bazaar and 
   GitHub/GitLab/Launchpad behind common interfaces.

2. **Script Agnostic** - Any command that can modify files should work as
   a codemod, regardless of language or tooling.

3. **Resumable Operations** - Support resuming from previous partial runs
   to handle large-scale operations gracefully.

4. **Batch Processing** - Enable reviewing changes before publishing when
   working with many repositories.

Future Considerations
---------------------

Performance Optimizations
~~~~~~~~~~~~~~~~~~~~~~~~~

* Parallel repository processing
* Incremental change detection
* Caching of forge API responses

See Also
--------

* Codemod protocol specification in ``codemod-protocol.md``
* Python API design in ``README.python.md``
* Implementation details in the source code