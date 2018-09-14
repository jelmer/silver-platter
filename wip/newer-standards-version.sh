#!/bin/sh
perl -p -i -e 's/^Standards-Version: .*/Standards-Version: 4.2.1/' debian/control
echo "Use most recent version in Standards-Version field rather than non-existant future version."
