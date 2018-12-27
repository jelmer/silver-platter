Scheduling
==========

There is a two step process to proposing changes:

 1. Generate a list of packages that need to be processed
 2. Process each package on a worker

Information per job
-------------------

 * Type of scheduling
 * Mode ("push", "propose", "attempt-push", "auto")
   + Where "auto" leaves the mode up to the runner, based on an exit
     code or tag in output.
 * Package name
 * Possible extra arguments (e.g. lintian tags to address)

Examples
~~~~~~~~

 * ("https://salsa.debian.org/samba-team/tdb", "propose",
    ["lintian-brush", "copyright-has-crs"])
 * ("https://salsa.debian.org/samba-team/talloc", "propose",
    ["lintian-brush"])
 * ("https://salsa.debian.org/samba-team/tdb", "attempt-push",
    ["merge-new-upstream"])
