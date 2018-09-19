#!/bin/sh
perl -p -i -e 's/\$\(PWD\)/\$\(CURDIR\)/' debian/rules
echo "debian/rukes: Avoid using \$(PWD) variable."
