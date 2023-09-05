# Silver-Platter Codemod Protocol, v1

The core of silver-platter are user-provided codemod commands, which get run in
version control checkouts to make changes.

Commands will be run in a clean VCS checkout, where they can make changes as
they deem fit. Changes should ideally be committed; by default pending changes
will be discarded (but silver-platter will warn about them, and --autocommit
can specified).

However, if commands just make changes and don't touch the VCS at all,
silver-platter will function in "autocommit" mode and create a single commit on
their behalf with a reasonable commit message.

Flags can be specified on the command-line or in a recipe:

* name (if not specified, taken from filename?)
* command to run
* merge proposal commit message (with jinja2 templating)
* merge proposal description, markdown/plain (with jinja2 templating)
* whether the command can resume
* mode ('push', 'attempt-push', 'propose') - defaults to 'attempt-push'
* optional propose threshold, with minimum value before merge proposals are created
* whether to autocommit (defaults to true?)
* optional URL to target (if different from base URL)

The command should exit with code 0 when successful (or no-op), and 1 otherwise. In
the case of failure, the branch is discarded.

If it is known that the command supports resuming, then a previous branch
may be loaded if present. The `SVP_RESUME` environment variable
will be set to a path to a JSON file with the previous runs metadata.
The command is expected to import any metadata about the older changes
and carry it forward.
If resuming is not supported then all older changes will be discarded
(and possibly made again by the command).

Environment variables that will be set:

* `SVP_API`: Silver-platter API major version number. Currently set to 1
* `COMMITTER`: Set to a committer identity (optional)
* `SVP_RESUME`: Set to a file path with JSON results from the last run, if
    available and if --resume is enabled.
* `SVP_RESULT`: Set to a (optional) path that should be created by the command
    with extra details

The output JSON should include the following fields:

* *code*: In case of an error, category of error that occurred. Special values are
  * *success*: Changes were successfully made
  * *nothing-to-do*: There were no relevant changes that could be made
* *transient*: Optional boolean indicating whether the error was transient
* *stage*: Optional list with the name of the stage the codemod was in when it failed
* *description*: Optional one-line text description of the error or changes made
* *value*: Optional integer with an indicator of the value of the changes made
* *tags*: Optional list of names of tags that should be included with the change (autodetected if not specified)
* *context*: Optional command-specific result data, made available during template expansion
* *target-branch-url*: URL for branch to target, if different from original URL

The *value* of a run can be used when e.g. prioritizing the publishing of results,
if there are multiple runs. It's only meaningful relative to the value of other
runs.

Debian operations
-----------------

For Debian branches, branches will be provided named according to
`DEP-14 <https://dep-team.pages.debian.net/deps/dep14/>`_.
The following environment variables will be set as well:

* `DEB_SOURCE`: Source package name
* `DEB_UPDATE_CHANGELOG`: Set to either update_changelog/leave_changelog (optional)
* `ALLOW_REFORMATTING`: boolean indicating whether reformatting is allowed
