#!/bin/sh
perl -p -i -e 's/^Homepage: http:\/\/github.com/Homepage: https:\/\/github.com/' debian/control
echo "Use secure URI in Homepage field."
echo
echo "Fixes lintian: homepage-field-uses-insecure-uri"
echo "https://lintian.debian.org/tags/vcs-field-uses-insecure-uri.html"
