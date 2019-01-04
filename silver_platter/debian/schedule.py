#!/usr/bin/python
# Copyright (C) 2018 Jelmer Vernooij <jelmer@jelmer.uk>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

from __future__ import absolute_import

__all__ = [
    'schedule',
    ]

import fnmatch
from io import StringIO

from breezy import trace, urlutils

from . import (
    get_source_package,
    source_package_vcs_url,
    NoSuchPackage,
    )
from .lintian import (
    available_lintian_fixers,
    download_latest_lintian_log,
    read_lintian_log,
    )
from .policy import (
    read_policy,
    apply_policy,
    )


def schedule(lintian_log, policy, propose_addon_only, packages, fixers, shuffle=False):
    if lintian_log:
        f = open(lintian_log, 'r')
    else:
        f = download_latest_lintian_log()

    with f:
        lintian_errs = read_lintian_log(f)

    with open(policy, 'r') as f:
        policy = read_policy(f)

    propose_addon_only = set(propose_addon_only)

    fixer_scripts = {}
    for fixer in available_lintian_fixers():
        for tag in fixer.lintian_tags:
            fixer_scripts[tag] = fixer

    available_fixers = set(fixer_scripts)
    if fixers:
        available_fixers = available_fixers.intersection(set(fixers))

    todo = set()
    if not packages:
        todo = set(lintian_errs.keys())
    else:
        for pkg_match in packages:
            todo.update(fnmatch.filter(lintian_errs.keys(), pkg_match))

    trace.note(
        "Considering %d packages for automatic change proposals",
        len(todo))

    todo = list(todo)

    if shuffle:
        import random
        random.shuffle(todo)
    else:
        todo.sort()

    for pkg in todo:
        errs = lintian_errs[pkg]

        fixers = available_fixers.intersection(errs)
        if not fixers:
            continue

        if not (fixers - propose_addon_only):
            continue

        try:
            pkg_source = get_source_package(pkg)
        except NoSuchPackage:
            trace.note('%s: not in apt sources', pkg)
            continue

        try:
            vcs_type, vcs_url = source_package_vcs_url(pkg_source)
        except urlutils.InvalidURL as e:
            trace.note('%s: %s', pkg, e.extra)
        except KeyError:
            trace.note('%s: no VCS URL found', pkg)
            continue

        mode, update_changelog, committer = apply_policy(policy, pkg_source)

        if mode == 'skip':
            trace.note('%s: skipping, per policy', pkg)
            continue

        command = ["lintian-brush"]
        if update_changelog == "update":
            command.append("--update-changelog")
        elif update_changelog == "leave":
            command.append("--no-update-changelog")
        command += list(fixers)
        yield (
            vcs_url, mode,
            {'COMMITTER': committer, 'PACKAGE': pkg},
            command)
