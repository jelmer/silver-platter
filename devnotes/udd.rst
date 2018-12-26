UDD
===

At the moment, packages that need work are found using a combination of:

 * `lintian.log` on https://lintian.debian.org/
 * the local machine's APT source list

This requires that the local system has a sources list available to begin
with and that is has an up to date source list. It also requires
unnecessarily parsing the entire lintian log file.

Rather, it should be possible to use UDD to query just those packages
that match the right criteria (specific maintainer/uploader,
lintian tags that lintian-brush supports, etc).
