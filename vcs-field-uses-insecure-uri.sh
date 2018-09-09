#!/bin/sh
perl -p -i -e 's/^Vcs-Git: git:\/\/github.com/Vcs-Git: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: http:\/\/salsa.debian.org/Vcs-Git: https:\/\/salsa.debian.org/' debian/control
echo "Use secure URI in Vcs-Git control header."
echo
echo "Fixes lintian: vcs-field-uses-insecure-uri"
echo "https://lintian.debian.org/tags/vcs-field-uses-insecure-uri.html"
