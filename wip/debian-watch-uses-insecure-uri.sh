#!/bin/sh
perl -p -i -e 's/^http:\/\/code.launchpad.net\//https:\/\/code.launchpad.net\//' debian/watch
echo "Use secure URI in debian/watch."
