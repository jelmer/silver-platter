#!/bin/sh
perl -p -i -e 's/Testsuite: autopkgtest\n//' debian/control
echo "Remove unnecessary 'Testsuite: autopkgtest' header."
echo
echo "Fixes lintian: unncessary-testsuite-autopkgtest-field"
