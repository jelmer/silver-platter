Command-line interface
======================

Example commands:

svp autopropose lp:brz-email ./some-script.py
svp autopropose --name=blah lp:brz-email ./some-script.py

svp hosters
svp login https://github.com/
svp login https://gitlab.com/
svp login https://salsa.debian.org/

debian-svp autopropose brz-email ./some-script.py
debian-svp lintian-brush samba
debian-svp lintian-brush --mode=propose samba
debian-svp lintian-brush --mode=push samba

debian-svp upload-pending tdb

debian-svp merge-upstream --no-build-verify tdb
