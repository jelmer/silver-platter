#!/bin/sh
if [ ! -d debian/source ]; then
    brz mkdir debian/source
fi
if [ -f debian/source/format ]; then
    echo "source format file already exists" >&2
    exit 1
fi
python>debian/source/format <<EOF
from debian.changelog import Changelog
with open('debian/changelog') as f:
  ch = Changelog(f, max_blocks=1)

if not ch.version.debian_revision:
  print("3.0 (native)")
else:
  print("3.0 (quilt)")
EOF
echo "Explicit specify source format."
