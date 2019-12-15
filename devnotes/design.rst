Releaser Tool
=============

* Specify timeout for a release
* Ability to manually trigger a release
* Use a custom PGP key (trusted by mine)
* Prometheus metrics

Specify a bit of config (protobuf?) that determines:

* Repository URL
* Bug Database
* Tag format
* Timeout
* NEWS file path(s)
* Tarball location
* Pypi location
* PGP key

Once we've determined we want to do a release:

* Check if CI state is green
* Clone master
* Commit:
 * Update NEWS to mark the new release
 * Make sure version strings elsewhere are correct
* Tag && sign tag
* Create a tarball
* Sign the tarball
* Upload the tarball
 + SCP
 + pypi
* Mark any news bugs in NEWS as fixed [later]
* Commit:
 * Update NEWS and version strings for next version
* Push changes to master and new tag

Use silver-platter?
