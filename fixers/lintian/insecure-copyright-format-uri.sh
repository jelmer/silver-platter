#!/bin/sh
perl -p -i -e 's/^(Format|Format-Specification): http:\/\/www.debian.org\/doc\/packaging-manuals\/copyright-format\/1.0.*/Format: https:\/\/www.debian.org\/doc\/packaging-manuals\/copyright-format\/1.0\//' debian/copyright
echo "Use secure copyright file specification URI."
