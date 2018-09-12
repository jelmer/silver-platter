#!/bin/sh
perl -p -i -e 's/^Vcs-Git: git:\/\/github.com/Vcs-Git: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: http:\/\/github.com/Vcs-Git: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: http:\/\/salsa.debian.org/Vcs-Git: https:\/\/salsa.debian.org/' debian/control
perl -p -i -e 's/^Vcs-Browser: http:\/\/salsa.debian.org/Vcs-Browser: https:\/\/salsa.debian.org/' debian/control
perl -p -i -e 's/^Vcs-Browser: git:\/\/github.com/Vcs-Browser: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Browser: http:\/\/github.com/Vcs-Browser: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: git:\/\/git.launchpad.net/Vcs-Git: https:\/\/git.launchpad.net/' debian/control
echo "Use secure URI in Vcs control header."
