#!/usr/bin/python3

from silver_platter.debian.lintian import download_latest_lintian_log

with open('lintian.log', 'w') as f, download_latest_lintian_log() as g:
    f.write(g.read())
