#!/bin/sh
perl -p -i -e 's/XS-Testsuite: autopkgtest\n//' debian/control
echo "Remove unnecessary XS-Testsuite field in debian/control."
