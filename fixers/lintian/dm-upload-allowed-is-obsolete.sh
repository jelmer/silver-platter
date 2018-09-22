#!/bin/sh
perl -p -i -e 's/DM-Upload-Allowed:.*yes\n//' debian/control
perl -p -i -e 's/Dm-Upload-Allowed:.*yes\n//' debian/control
echo "Remove unnecessary DM-Upload-Allowed field in debian/control."
