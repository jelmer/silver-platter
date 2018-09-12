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

from breezy.plugins.propose.propose import UnsupportedHoster
from breezy.plugins.propose.autopropose import autopropose

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("packages", nargs='*')
parser.add_argument("--fixers", help="Fixers to run.", type=str, action='append')
parser.add_argument("--ignore", help="Packages to ignore.", type=str, action='append')
args = parser.parse_args()

fixer_scripts = {}

for n in os.listdir('fixers'):
    fixer_scripts[os.path.splitext(n)[0]] = os.path.abspath(
            os.path.join('fixers', n))

todo = {}

with open('lintian.log', 'r') as f:
    for l in f:
        cs = l.split(' ')
        if cs[0] not in ('E:', 'W:', 'I:', 'P:'):
            continue
        pkg = cs[1]
        if ((args.ignore and pkg in args.ignore) or
            (args.packages and not pkg in args.packages)):
            continue
        err = cs[5].strip()
        if args.fixers and not err in args.fixers:
            continue
        if err in fixer_scripts:
            todo.setdefault(pkg, set()).add(err)


class NoChanges(Exception):
    """Script didn't make any changes."""


class ScriptFailed(Exception):
    """Script failed to run."""


def run_lintian_fixer(branch, fixer):
    note('Running fixer %s on %s', fixer, branch.user_url)
    script = fixer_scripts[fixer]
    local_tree = branch.controldir.create_workingtree()
    p = subprocess.Popen(script, cwd=local_tree.basedir, stdout=subprocess.PIPE)
    (description, err) = p.communicate("")
    if p.returncode != 0:
        raise ScriptFailed("Script %s failed with error code %d" % (
                script, p.returncode))

    with local_tree.lock_read():
        if list(local_tree.iter_changes(local_tree.basis_tree())):
            subprocess.check_call(
                ["dch", "--no-auto-nmu", description.splitlines()[0]],
                cwd=local_tree.basedir)

    description += "\n"
    description += "Fixes lintian: %s\n" % fixer
    description += "See https://lintian.debian.org/tags/%s.html for more details.\n" % fixer

    try:
        local_tree.commit(description, allow_pointless=False)
    except PointlessCommit:
        raise NoChanges("Script didn't make any changes")
    # TODO(jelmer): Run sbuild & verify lintian warning is gone
    return description


for pkg, fixers in sorted(todo.items()):
    try:
        branch = Branch.open("apt:%s" % pkg)
    except socket.error:
        note('%s: ignoring, socket error', pkg)
    except urlutils.InvalidURL as e:
        if ('unsupported VCSes' in e.extra or
            'no URLs found' in e.extra or
            'only Vcs-Browser set' in e.extra):
            note('%s: %s', pkg, e.extra)
        else:
            raise
    except errors.NotBranchError as e:
        note('%s: Branch does not exist: %s', pkg, e)
    except errors.UnsupportedProtocol:
        note('%s: Branch available over unsupported protocol', pkg)
    except errors.ConnectionError as e:
        note('%s: %s', pkg, e)
    except errors.PermissionDenied as e:
        note('%s: %s', pkg, e)
    except errors.RedirectRequested as e:
        # TODO(jelmer): Remove this once breezy's git support properly handles redirects.
        # pad.lv/1791535
        note('%s: %s', pkg, e)
    else:
        for fixer in fixers:
            try:
                mp = autopropose(
                    branch,
                    lambda local_branch: run_lintian_fixer(local_branch, fixer),
                    name=fixer)
            except NoChanges:
                pass
            except ScriptFailed:
                note('%s: Script for %s failed to run', pkg, fixer)
            except errors.DivergedBranches:
                note('%s: Already proposed: %s', pkg, fixer)
            except UnsupportedHoster:
                note('%s: Hoster unsupported', pkg)
            except errors.AlreadyBranchError:
                note('%s: Already proposed: %s', pkg, fixer)
            else:
                note('%s: Proposed fix for %s: %s', pkg, fixer, mp.url)
