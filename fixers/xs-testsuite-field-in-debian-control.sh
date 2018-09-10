#!/bin/sh
perl -p -i -e 's/XS-Testsuite: autopkgtest\n//' debian/control
dch "Remove unnecessary XS-Testsuite field in debian/control."
echo "Remove unnecessary XS-Testsuite field in debian/control."
echo
echo "Fixes lintian: unncessary-testsuite-autopkgtest-field"
echo "See https://lintian.debian.org/tags/xs-testsuite-field-in-debian-control.html".
