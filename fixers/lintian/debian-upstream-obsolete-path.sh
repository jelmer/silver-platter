#!/bin/bash
if [[ -f debian/upstream ]]; then
	mv debian/upstream debian/upstream-metadata.yaml
fi
mkdir -p debian/upstream
bzr add debian/upstream
test -f debian/upstream-metadata && mv debian/upstream-metadata debian/upstream/metadata
test -f debian/upstream-metadata.yaml && mv debian/upstream-metadata.yaml debian/upstream/metadata
echo "Move upstream metadata to debian/upstream/metadata."
