#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij <jelmer@jelmer.uk>
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

import logging
import os
import sys
from typing import Any, List, Optional, Dict



class ChangerError(Exception):
    def __init__(
            self, category: str, summary: str, original: Optional[Exception] = None, details: Any = None
    ):
        self.category = category
        self.summary = summary
        self.original = original
        self.details = details


class ChangerResult(object):
    def __init__(
        self,
        description: Optional[str],
        mutator: Any,
        tags: Optional[Dict[str, bytes]] = None,
        value: Optional[int] = None,
        proposed_commit_message: Optional[str] = None,
        title: Optional[str] = None,
        labels: Optional[List[str]] = None,
        sufficient_for_proposal: bool = True,
    ):
        self.description = description
        self.mutator = mutator
        self.tags = tags or {}
        self.value = value
        self.proposed_commit_message = proposed_commit_message
        self.title = title
        self.labels = labels
        self.sufficient_for_proposal = sufficient_for_proposal
