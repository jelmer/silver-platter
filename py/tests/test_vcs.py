#!/usr/bin/python
# Copyright (C) 2024 Jelmer Vernooij
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

import os
import pickle

from breezy.tests import TestCase, TestCaseWithTransport

from silver_platter import (
    BranchError,
    BranchMissing,
    BranchRateLimited,
    BranchUnsupported,
    _open_branch,
)


class OpenBranchTests(TestCaseWithTransport):
    def test_simple(self):
        a = self.make_branch("target")
        b = _open_branch(a.base)
        self.assertEqual(a.base, b.base)

    def test_missing(self):
        url = f"file://{os.getcwd()}/nonexistent"
        e = self.assertRaises(BranchMissing, _open_branch, url)
        self.assertIsInstance(e.url, str)
        self.assertIsInstance(e.message, str)
        self.assertEqual(e.url, url)


class BranchErrorTests(TestCase):
    def test_rate_limited_keeps_retry_after(self):
        e = BranchRateLimited("https://example.com/repo", "slow down", 60.0)
        self.assertIsInstance(e, BranchError)
        self.assertEqual("https://example.com/repo", e.url)
        self.assertEqual("slow down", e.message)
        self.assertEqual(60.0, e.retry_after)

    def test_rate_limited_without_retry_after(self):
        e = BranchRateLimited("https://example.com/repo", "slow down")
        self.assertIsNone(e.retry_after)

    def test_unsupported_keeps_vcs(self):
        e = BranchUnsupported("svn://example.com/repo", "not supported", "svn")
        self.assertIsInstance(e, BranchError)
        self.assertEqual("svn://example.com/repo", e.url)
        self.assertEqual("not supported", e.message)
        self.assertEqual("svn", e.vcs)

    def test_unsupported_without_vcs(self):
        e = BranchUnsupported("svn://example.com/repo", "not supported")
        self.assertIsNone(e.vcs)

    def test_rate_limited_takes_retry_after_by_keyword(self):
        e = BranchRateLimited(
            "https://example.com/repo", "slow down", retry_after=5.0
        )
        self.assertEqual(5.0, e.retry_after)
        self.assertEqual(
            ("https://example.com/repo", "slow down", 5.0), e.args
        )

    def test_unsupported_takes_vcs_by_keyword(self):
        e = BranchUnsupported(
            "svn://example.com/repo", "not supported", vcs="svn"
        )
        self.assertEqual("svn", e.vcs)
        self.assertEqual(
            ("svn://example.com/repo", "not supported", "svn"), e.args
        )

    def test_rate_limited_is_caught_as_branch_error(self):
        def raise_rate_limited():
            raise BranchRateLimited(
                "https://example.com/repo", "slow down", 60.0
            )

        e = self.assertRaises(BranchError, raise_rate_limited)
        self.assertEqual(60.0, e.retry_after)

    def test_rate_limited_pickles(self):
        e = pickle.loads(
            pickle.dumps(
                BranchRateLimited(
                    "https://example.com/repo", "slow down", 60.0
                )
            )
        )
        self.assertIsInstance(e, BranchRateLimited)
        self.assertEqual("https://example.com/repo", e.url)
        self.assertEqual(60.0, e.retry_after)
