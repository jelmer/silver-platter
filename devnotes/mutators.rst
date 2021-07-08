Commands will be run in a clean VCS checkout, where
they can make changes as they deem fit. Changes should be committed; by
default pending changes will be discarded (but silver-platter will
warn about them, and --autocommit can specified).

Flags can be specified on the command-line or in a recipe:

 * name (if not specified, taken from filename?)
 * command to run
 * merge proposal commit message (with jinja2 templating)
 * merge proposal description, markdown/plain (with jinja2 templating)
 * whether the command can resume
 * mode ('push', 'attempt-push', 'propose') - defaults to 'attempt-push'
 * optional propose threshold, with minimum value before merge proposals
   are created
 * whether to autocommit (defaults to true?)

The command should exit with code 0 when successful, and 1 otherwise. In
the case of failure, the branch is discarded.

If it is known that the command supports resuming, then a previous branch
may be loaded if present. The SVP_RESUME environment variable
will be set to a path to a JSON file with the previous runs metadata.
The command is expected to import any metadata about the older changes
and carry it forward.
If resuming is not supported then all older changes will be discarded
(and possibly made again by the command).

Environment variables that will be set:

 * SVP_API: Currently set to 1
 * COMMITTER: Set to a committer identity (optional)
 * SVP_RESUME: Set to a file path with JSON results from the last run,
   if available and if --resume is enabled.
 * SVP_RESULT: Set to a (optional) path that should be created by the command
     with extra details

The output JSON should include the following fields:

 * description: Optional one-line text description of the error or changes made
 * value: Optional integer with an indicator of the value of the changes made
 * tags: Optional list of names of tags that should be included with the change
   (autodetected if not specified)
 * context: Optional command-specific result data, made available
        during template expansion

Debian operations
-----------------

For Debian branches, branches will be provided named according to DEP-13.
The following environment variables will be set as well:

 * DEB_SOURCE: Source package name
 * DEB_UPDATE_CHANGELOG: Set to either update_changelog/leave_changelog (optional)
 * ALLOW_REFORMATTING: boolean indicating whether reformatting is allowed

Required Changes
================

1) add support for providing SVP_RESULT environment variable and reading it
2) gradually move existing mutators over:
 + lintian-brush
 + deb-scrub-obsolete
 + apply-multiarch-hints
3) move all logic for lintian-brush into actual lintian-brush binary
 + Add Enhances: silver-platter to lintian-brush
4) move detect_gbp_dch out of lintian-brush
5) add ability to specify candidate list (yaml) to debian-svp and svp
