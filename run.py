#!/usr/bin/python
from debian.deb822 import Deb822
from email.utils import parseaddr
import fnmatch
import os
import shutil
import subprocess
import socket
import sys
import tempfile

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
from breezy.transport import get_transport

from breezy.plugins.propose.propose import (
    get_hoster,
    UnsupportedHoster,
    )

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("packages", nargs='*')
parser.add_argument("--fixers", help="Fixers to run.", type=str, action='append')
parser.add_argument("--ignore", help="Packages to ignore.", type=str, action='append', default=[])
parser.add_argument("--ignore-file", help="File to load packages to ignore from.",
                    type=str, action='append', default=[])
parser.add_argument('--just-push-file', type=str, action='append', default=[],
                    help=('File with maintainer emails for which just to push, '
                          'rather than propose changes.'))
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

just_push_maintainers = set()
for just_push_file in args.just_push_file:
    with open(just_push_file, 'r') as f:
        for l in f:
            just_push_maintainers.add(l.split('#')[0].strip())


class NoChanges(Exception):
    """Script didn't make any changes."""


class ScriptFailed(Exception):
    """Script failed to run."""


def run_lintian_fixer(local_tree, fixer):
    note('Running fixer %s on %s', fixer, local_tree.branch.user_url)
    script = fixer_scripts[fixer]
    p = subprocess.Popen(script, cwd=local_tree.basedir, stdout=subprocess.PIPE)
    (description, err) = p.communicate("")
    if p.returncode != 0:
        raise ScriptFailed("Script %s failed with error code %d" % (
                script, p.returncode))

    summary = description.splitlines()[0]

    with local_tree.lock_read():
        if list(local_tree.iter_changes(local_tree.basis_tree())):
            subprocess.check_call(
                ["dch", "--no-auto-nmu", summary],
                cwd=local_tree.basedir)

    description += "\n"
    description += "Fixes lintian: %s\n" % fixer
    description += "See https://lintian.debian.org/tags/%s.html for more details.\n" % fixer

    try:
        local_tree.commit(description, allow_pointless=False)
    except PointlessCommit:
        raise NoChanges("Script didn't make any changes")
    # TODO(jelmer): Run sbuild & verify lintian warning is gone
    return summary


def run_lintian_fixers(local_branch, fixers):
    local_tree = local_branch.controldir.create_workingtree()
    mp_description = []
    for fixer in fixers:
        try:
            mp_description.append(run_lintian_fixer(local_tree, fixer))
        except ScriptFailed:
            note('%s: Script for %s failed to run', pkg, fixer)
        except NoChanges:
            pass
    return mp_description


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

note("Considering %d packages for automatic change proposals", len(todo))

for pkg in sorted(todo):
    errs = lintian_errs[pkg]

    fixers = available_fixers.intersection(errs)
    if not fixers:
        continue

    try:
        main_branch = Branch.open("apt:%s" % pkg)
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
        try:
            [name] = fixers
        except ValueError:  # more than one fixer
            name = "lintian-fixes"
        try:
            hoster = get_hoster(main_branch)
        except UnsupportedHoster:
            note('%s: Hoster unsupported', pkg)
            continue
        try:
            existing_branch = hoster.get_derived_branch(main_branch, name=name)
        except errors.NotBranchError:
            pass
        else:
            # TODO(jelmer): If this is a branch named 'lintian-fixes', verify
            # that all available fixers were included?
            note('%s: Already proposed: %s', pkg, name)
            continue
        td = tempfile.mkdtemp()
        try:
            # preserve whatever source format we have.
            to_dir = main_branch.controldir.sprout(
                    get_transport(td).base, None, create_tree_if_local=False,
                    source_branch=main_branch, stacked=main_branch._format.supports_stacking())
            local_branch = to_dir.open_branch()
            orig_revid = local_branch.last_revision()
            mp_description = run_lintian_fixers(local_branch, fixers)
            if local_branch.last_revision() == orig_revid:
                continue
            revtree = local_branch.repository.revision_tree(local_branch.last_revision())
            with revtree.lock_read(), revtree.get_file('debian/control') as fh:
                control = Deb822(fh)
                just_push = False
                if parseaddr(control["Maintainer"])[1] in just_push_maintainers:
                    just_push = True
                for uploader in control.get("Uploaders", "").split(","):
                    if parseaddr(uploader)[1] in just_push_maintainers:
                        just_push = True
            if just_push:
                push_url = hoster.get_push_url(main_branch)
                note('%s: pushing to %s', pkg, push_url)
                local_branch.push(Branch.open(push_url))
            else:
                try:
                    remote_branch, public_branch_url = hoster.publish_derived(
                        local_branch, main_branch, name=name, overwrite=False)
                except errors.DivergedBranches:
                    note('%s: Already proposed: %s', pkg, name)
                    continue
                proposal_builder = hoster.get_proposer(remote_branch, main_branch)
                if len(mp_description) > 1:
                    mp_description = ["Fix some issues reported by lintian\n"] + [
                            ("* %s\n" % l) for l in mp_description]
                mp = proposal_builder.create_proposal(
                    description=''.join(mp_description), labels=[])
                note('%s: Proposed fix for %r: %s', pkg, name, mp.url)
        finally:
            shutil.rmtree(td)
