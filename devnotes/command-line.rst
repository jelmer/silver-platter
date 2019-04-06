Command-line interface
======================

Example commands:

svp run lp:brz-email /tmp/some-script.py
svp run --name=blah lp:brz-email /tmp/some-script.py
svp run --mode=attempt-push lp:brz-email /tmp/some-script.py

svp hosters
svp login https://github.com/
svp login https://gitlab.com/
svp login https://salsa.debian.org/

debian-svp run brz-email ./some-script.py

debian-svp lintian-brush samba
debian-svp lintian-brush --mode=propose samba
debian-svp lintian-brush --mode=push samba

debian-svp upload-pending tdb

debian-svp merge-upstream --no-build-verify tdb
