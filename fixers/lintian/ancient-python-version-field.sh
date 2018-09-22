#!/bin/sh
# Remove anything that involves python 2.6, 2.7, 3.3
perl -p -i -e 's/X-Python-Version: >= 2\..\n//' debian/control
perl -p -i -e 's/X-Python3-Version: >= 3.[01234]\n//' debian/control
echo "Remove unnecessary X-Python{,3}-Version field in debian/control."
