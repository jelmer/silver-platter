#!/bin/sh
perl -p -i -e 's/^Format-Specification: .*/Format: https:\/\/www.debian.org\/doc\/packaging-manuals\/copyright-format\/1.0\//' debian/copyright
echo "Use correct machine-readable copyright file URI."
echo
echo "Fixes lintian: out-of-date-copyright-format-uri"
echo "https://lintian.debian.org/tags/out-of-date-copyright-format-uri.html"
