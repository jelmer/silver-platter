#!/bin/sh
perl -p -i -e 's/^http:\/\/code.launchpad.net\//https:\/\/code.launchpad.net\//' debian/watch
uscan --no-download || exit 1
echo "Use secure URI in debian/watch."
