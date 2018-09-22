#!/bin/sh
perl -p -i -e 's/^Maintainer: .*<packages@qa.debian.org>/Maintainer: Debian QA Group <packages@qa.debian.org>/' debian/control
echo "Fix Debian QA group name."
