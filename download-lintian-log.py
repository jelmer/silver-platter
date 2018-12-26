#!/usr/bin/python3

from silver_platter.debian.lintian import download_latest_lintian_log

with open('lintian.log', 'wb') as f:
    f.write(download_latest_lintian_log())
