#!/usr/bin/python3

import gzip
import re
import urllib.parse
import urllib3

http = urllib3.PoolManager()

BASE = 'https://lintian.debian.org/'

page = http.request('GET', BASE)
resource = re.search(b'<a href="(.*.gz)">lintian.log.gz<\\/a>', page.data).group(1)

log = http.request('GET', urllib.parse.urljoin(BASE, resource.decode('ascii')))
with open('lintian.log', 'wb') as f:
    f.write(gzip.decompress(log.data))
