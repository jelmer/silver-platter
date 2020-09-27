Mutators live in /usr/share/silver-platter/mutators.

Each mutators is a binary that will be run in a clean VCS checkout, where
they can make changes as they deem fit. Changes should be committed; pending
changes will be discarded.

The arguments specified on the command-line to silver-platter are
passed onto the mutator.

The mutator should write JSON to standard out and use exit code 0.
It can report errors to standard error.  If it returns any other result it is
considered to have failed.

The output JSON should include the following fields:

 * result-code: Optional error code - a string of some sort
 * description: Optional one-line text description of the error or changes made
 * value: Optional integer with an indicator of the value of the changes made
 * suggested-branch-name: Optional suggested branch name
 * auxiliary-branches: Optional list of names of additional branches that
      should be included with the change
 * tags: Optional list of names of tags that should be included with the change
 * merge-proposal: Dictionary with information for merge proposal
   * sufficient: Boolean indicating whether this change is sufficient to be
     proposed as a merge
   * commit-message: Optional suggested commit message
   * title: Optional title
   * description-plain: Description for merge proposal (in plain text)
   * description-markdown: Optional description for merge proposal (in markdown)
 * mutator: Optional mutator-specific result data

Environment variables that will be set:

 * SILVER_PLATTER_API: Currently set to 1
 * BASE_METADATA: Set to a file path with JSON results from the last run, if
   available

For Debian mutators, the following will be set as well:

 * PACKAGE: Source package name
 * UPDATE_CHANGELOG: Set to either update_changelog/leave_changelog (optional)
 * ALLOW_REFORMATTING: boolean indicating whether reformatting is allowed
 * COMMITTER: Set to a committer identity (optional)

Mutators should support --help, so that "svp mutator --help" can be forwarded.

Required Changes
================

1) mutators can be installed in /usr/lib/silver-platter and /usr/lib/silver-platter/debian, possibly just symlinks?
 + Also, with an environment variable to override?
 + Possibly also just allow specifying the mutator path as an argument? "debian-svp ../lintian-brush"
2) "sv --help" or "debian-svp --help" will list all relevant mutators
3) start running current mutators as scripts from within svp itself
4) move all logic for lintian-brush into actual lintian-brush binary
 + Add Enhances: silver-platter to lintian-brush
5) move detect_gbp_dch out of lintian-brush
6) avoid add_changelog_entry from lintian-brush in rrr and orphan
7) add ability to specify candidate list to debian-svp and svp
