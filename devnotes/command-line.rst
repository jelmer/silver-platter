Command-line interface
======================

Example commands:

svp run lp:brz-email /tmp/some-script.py
svp run --name=blah lp:brz-email /tmp/some-script.py
svp run -f some-script.yaml lp:brz-email

svp hosters
svp login https://github.com/
svp login https://gitlab.com/
svp login https://salsa.debian.org/

debian-svp run brz-email ./some-script.py

debian-svp run -f lintian-brush.yaml samba
debian-svp run -f lintian-brush.yaml --mode=propose samba
debian-svp run -f lintian-brush.yaml --mode=push samba

debian-svp upload-pending tdb

debian-svp run -f new-upstream-release.yaml --no-build-verify tdb
