#!/usr/bin/python
import fnmatch
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
import breezy.plugins.launchpad
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
parser.add_argument("--ignore", help="Packages to ignore.", type=str, action='append', default=[])
parser.add_argument("--ignore-file", help="File to load packages to ignore from.",
                    type=str, action='append', default=[])
args = parser.parse_args()

fixer_scripts = {}

for n in os.listdir('fixers'):
    if n.endswith("~"):
        continue
    fixer_scripts[os.path.splitext(n)[0]] = os.path.abspath(
            os.path.join('fixers', n))

def read_lintian_log(f):
    lintian_errs = {}
    for l in f:
        cs = l.split(' ')
        if cs[0] not in ('E:', 'W:', 'I:', 'P:'):
            continue
        pkg = cs[1]
        err = cs[5].strip()
        lintian_errs.setdefault(pkg, set()).add(err)
    return lintian_errs


with open('lintian.log', 'r') as f:
    lintian_errs = read_lintian_log(f)


ignore_packages = set()
for ignore_match in args.ignore:
    ignore_packages.update(fnmatch.filter(lintian_errs.keys(), ignore_match))

for ignore_file in args.ignore_file:
    with open(ignore_file, 'rb') as f:
        for l in f:
            ignore_packages.add(l.split('#')[0].strip())


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


available_fixers = set(fixer_scripts)
if args.fixers:
    available_fixers = available_fixers.intersection(set(args.fixers))


todo = set()
if not args.packages:
    todo = set(lintian_errs.keys())
else:
    for pkg_match in args.packages:
        todo.update(fnmatch.filter(lintian_errs.keys(), pkg_match))

todo = todo - ignore_packages

for pkg in sorted(todo):
    errs = lintian_errs[pkg]

    fixers = available_fixers.intersection(errs)
    if not fixers:
        continue

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
    except errors.InvalidHttpResponse as e:
        note('%s: %s', pkg, e)
    except errors.TransportError as e:
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
