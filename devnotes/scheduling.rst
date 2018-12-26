Scheduling
==========

There is a two step process to proposing changes:

 1. Generate a list of packages that need to be processed
 2. Process each package on a worker

Information per job
-------------------

 * Type of scheduling
 * Package name
 * Possible extra arguments (e.g. lintian tags to address)

Examples
~~~~~~~~

 * ("tdb", ["lintian-brush", "copyright-has-crs"])
 * ("talloc", ["lintian-brush"])
 * ("tdb", ["merge-new-upstream"])
