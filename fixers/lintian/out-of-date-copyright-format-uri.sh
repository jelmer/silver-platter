#!/bin/sh
perl -p -i -e 's/^(Format|Format-Specification): .*/Format: https:\/\/www.debian.org\/doc\/packaging-manuals\/copyright-format\/1.0\//' debian/copyright
echo "Use correct machine-readable copyright file URI."
