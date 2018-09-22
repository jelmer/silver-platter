#!/bin/sh
perl -p -i -e 's/^http:\/\/code.launchpad.net\//https:\/\/code.launchpad.net\//' debian/watch
perl -p -i -e 's/^http:\/\/launchpad.net\//https:\/\/launchpad.net\//' debian/watch
perl -p -i -e 's/^http:\/\/ftp.gnu.org\//https:\/\/ftp.gnu.org\//' debian/watch
echo "Use secure URI in debian/watch."
