#!/bin/sh
perl -p -i -e 's/XC-Package-Type: (.*)\n/Package-Type: \1\n/' debian/control
echo "Replace XC-Package-Type with Package-Type."
