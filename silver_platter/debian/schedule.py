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
    'schedule_udd',
    'schedule_ubuntu',
    ]

from breezy import trace

from . import (
    convert_debian_vcs_url,
    )
from .policy import (
    read_policy,
    apply_policy,
    )
from .udd import UDD


def schedule_ubuntu(policy, propose_addon_only, packages, shuffle=False):
    udd = UDD.public_udd_mirror()

    with open(policy, 'r') as f:
        policy = read_policy(f)

    for package in udd.iter_ubuntu_source_packages(
            packages if packages else None, shuffle=shuffle):
        try:
            vcs_url = convert_debian_vcs_url(package.vcs_type, package.vcs_url)
        except ValueError as e:
            trace.note('%s: %s', package.name, e)
            continue
        mode, update_changelog, committer = apply_policy(
            policy, package.name, package.maintainer_email,
            package.uploader_emails)

        if mode == 'skip':
            trace.note('%s: skipping, per policy', package.name)
            continue

        command = ["lintian-brush"]
        if update_changelog == "update":
            command.append("--update-changelog")
        elif update_changelog == "leave":
            command.append("--no-update-changelog")
        elif update_changelog == "auto":
            pass
        else:
            raise ValueError(
                "Invalid value %r for update_changelog" % update_changelog)
        yield (
            vcs_url, mode,
            {'COMMITTER': committer, 'PACKAGE': package.name},
            command)


def schedule_udd(policy, propose_addon_only, packages, available_fixers,
                 shuffle=False):
    udd = UDD.public_udd_mirror()

    with open(policy, 'r') as f:
        policy = read_policy(f)

    for package, tags in udd.iter_source_packages_by_lintian(
            available_fixers, packages if packages else None, shuffle=shuffle):
        try:
            vcs_url = convert_debian_vcs_url(package.vcs_type, package.vcs_url)
        except ValueError as e:
            trace.note('%s: %s', package.name, e)
            continue

        mode, update_changelog, committer = apply_policy(
            policy, package.name, package.maintainer_email,
            package.uploader_emails)

        if mode == 'skip':
            trace.note('%s: skipping, per policy', package.name)
            continue

        command = ["lintian-brush"]
        if update_changelog == "update":
            command.append("--update-changelog")
        elif update_changelog == "leave":
            command.append("--no-update-changelog")
        elif update_changelog == "auto":
            pass
        else:
            raise ValueError(
                "Invalid value %r for update_changelog" % update_changelog)
        command += list(tags)
        yield (
            vcs_url, mode,
            {'COMMITTER': committer, 'PACKAGE': package.name},
            command)
