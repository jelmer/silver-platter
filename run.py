#!/usr/bin/python
import os
import subprocess
import socket
import sys

if os.name == "posix":
    import locale
    locale.setlocale(locale.LC_ALL, '')
    # Use better default than ascii with posix filesystems that deal in bytes
    # natively even when the C locale or no locale at all is given. Note that
    # we need an immortal string for the hack, hence the lack of a hyphen.
    sys._brz_default_fs_enc = "utf8"

import breezy
breezy.initialize()
import breezy.git
import breezy.bzr
import breezy.plugins.debian # for apt: urls
from breezy import (
    errors,
    urlutils,
    )

from breezy.branch import Branch
from breezy.commit import PointlessCommit
from breezy.trace import note

from breezy.plugins.propose.autopropose import autopropose

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("packages", nargs='*')
args = parser.parse_args()

fixers = {}

for n in os.listdir('fixers'):
    fixers[os.path.splitext(n)[0]] = os.path.abspath(os.path.join('fixers', n))

todo = []

with open('lintian.log', 'r') as f:
    for l in f:
        cs = l.split(' ')
        if cs[0] not in ('E:', 'W:', 'I:', 'P:'):
            continue
        pkg = cs[1]
        if args.packages and not pkg in args.packages:
            continue
        err = cs[5]
        if err in fixers:
            todo.append((pkg, err))


def run_fixer(branch, fixer):
    note('Running fixer %s on %s', fixer, branch.user_url)
    script = fixers[fixer]
    local_tree = branch.controldir.create_workingtree()
    p = subprocess.Popen(script, cwd=local_tree.basedir, stdout=subprocess.PIPE)
    (description, err) = p.communicate("")
    if p.returncode != 0:
        raise Exception("Script %s failed with error code %d" % (
                script, p.returncode))
    try:
        local_tree.commit(description, allow_pointless=False)
    except PointlessCommit:
        raise Exception("Script didn't make any changes")
    # TODO(jelmer): Run sbuild & verify lintian warning is gone
    return description


for pkg, fixer in todo:
    try:
        branch = Branch.open("apt:%s" % pkg)
    except socket.error:
        note('%s: ignoring, socket error', pkg)
    except urlutils.InvalidURL as e:
        if 'unsupported VCSes' in e.extra:
            note('%s: %s', pkg, e.extra)
        else:
            raise
    except errors.NotBranchError as e:
        note('%s: Branch does not exist: %s', pkg, e)
    else:
        autopropose(branch, lambda local_branch: run_fixer(local_branch, fixer), name=fixer)
