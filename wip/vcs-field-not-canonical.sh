#!/bin/sh
perl -p -i -e 's/^Vcs-Browser: http:\/\/salsa.debian.org\/([^/]+/[^/]+)\.git/Vcs-Browser: https:\/\/salsa.debian.org/$1' debian/control
echo "Use canonical URI in Vcs control header."

