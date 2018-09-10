#!/bin/sh
perl -p -i -e 's/^Homepage: http:\/\/github.com/Homepage: https:\/\/github.com/' debian/control
echo "Use secure URI in Homepage field."
