#!/bin/sh
perl -p -i -e 's/^Vcs-Git: (git@|)salsa.debian.org:/Vcs-Git: https:\/\/salsa.debian.org\//' debian/control
perl -p -i -e 's/^Vcs-Git: (git@|)gitlab.com:/Vcs-Git: https:\/\/gitlab.com\//' debian/control
perl -p -i -e 's/^Vcs-Git: (git@|)github.com:/Vcs-Git: https:\/\/github.com\//' debian/control
perl -p -i -e 's/^Vcs-Git: (git@|)gitorious.org:/Vcs-Git: https:\/\/gitorious.org\//' debian/control
echo "Use recommended URI format in Vcs header."
