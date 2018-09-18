#!/bin/sh
if [ ! -f debian/source.lintian-overrides ]; then
    echo "File missing" >&2
    exit 1
fi
if [ ! -d debian/source ]; then
    brz mkdir debian/source
fi
brz rename debian/source.lintian-overrides debian/source/lintian-overrides
echo "Move source package lintian overrides to debian/source."
