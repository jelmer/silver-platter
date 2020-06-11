Mutators live in /usr/share/silver-platter/mutators.

Each mutators is a binary that will be run in a clean VCS checkout, where
they can make changes as they deem fit. Changes should be committed.

The arguments specified on the command-line to silver-platter are
passed onto the mutator.

The mutator should write JSON to standard out and use exit code 0.
If it returns any other result it is considered to have failed.

The output JSON should include the following fields:

 * result_code: Optional error code
 * description: Optional one-line text description of the error or changes made
 * value: Optional integer with an indicator of the value of the changes made
 * suggested-branch-name: Optional suggested branch name
 * auxiliary-branches: Optional list of names of additional branches that
      should be included with the change
 * tags: Optional list of names of tags that should be included with the change
 * merge_proposal: Dictionary with information for merge proposal
   * sufficient: Boolean indicating whether this change is sufficient to be proposed as a merge
   * commit-message: Optional suggested commit message
   * title: Optional title
   * description-plain: Description for merge proposal (in plain text)
   * description-markdown: Optional description for merge proposal (in markdown)
 * mutator: Optional mutator-specific data

Environment variables that will be set:

 * SILVER_PLATTER_API: Currently set to 1
 * BASE_METADATA: Set to a file path with JSON results from the last run, if available

For Debian mutators, the following will be set as well:

 * PACKAGE: Source package name
 * UPDATE_CHANGELOG: Set to either update_changelog/leave_changelog (optional)
 * COMMITTER: Set to a committer identity (optional)
