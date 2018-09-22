#!/bin/sh
echo "Use secure URI in Vcs control header."
perl -p -i -e 's/^Vcs-Git: git:\/\/github.com/Vcs-Git: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: http:\/\/github.com/Vcs-Git: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: http:\/\/salsa.debian.org/Vcs-Git: https:\/\/salsa.debian.org/' debian/control
perl -p -i -e 's/^Vcs-Browser: http:\/\/salsa.debian.org/Vcs-Browser: https:\/\/salsa.debian.org/' debian/control
perl -p -i -e 's/^Vcs-Browser: git:\/\/github.com/Vcs-Browser: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Browser: http:\/\/github.com/Vcs-Browser: https:\/\/github.com/' debian/control
perl -p -i -e 's/^Vcs-Git: git:\/\/git.launchpad.net/Vcs-Git: https:\/\/git.launchpad.net/' debian/control
perl -p -i -e 's/^Vcs-Bzr: http:\/\/code.launchpad.net/Vcs-Bzr: https:\/\/code.launchpad.net/' debian/control
perl -p -i -e 's/^Vcs-Browser: http:\/\/code.launchpad.net/Vcs-Browser: https:\/\/code.launchpad.net/' debian/control
if grep "Vcs-Bzr: lp:" debian/control >/dev/null; then
  echo
  echo "The lp: prefix gets expanded to http://code.launchpad.net/ for "
  echo "users that are not logged in on some versions of Bazaar."
fi
perl -p -i -e 's/^Vcs-Bzr: lp:/Vcs-Bzr: https:\/\/code.launchpad.net\//' debian/control
