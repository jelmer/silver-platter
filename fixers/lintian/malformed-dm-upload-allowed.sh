#!/bin/sh
perl -p -i -e 's/DM-Upload-Allowed:.*\n//' debian/control
echo "Remove malformed and unnecessary DM-Upload-Allowed field in debian/control."
