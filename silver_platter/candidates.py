#!/usr/bin/python
# Copyright (C) 2021 Jelmer Vernooij <jelmer@jelmer.uk>
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

from dataclasses import dataclass
from typing import Optional, List
import yaml


@dataclass
class Candidate(object):
    """Candidate."""

    url: str
    branch: Optional[str] = None
    subpath: str = ''

    @classmethod
    def from_yaml(cls, d):
        if isinstance(d, dict):
            return cls(
                url=d.get('url'),
                branch=d.get('branch'),
                subpath=d.get('path'),
                )
        elif isinstance(d, str):
            return cls(url=d)
        else:
            raise TypeError(d)


@dataclass
class CandidateList(object):
    """Candidate list."""

    candidates: List[Candidate]

    def __iter__(self):
        return iter(self.candidates)

    @classmethod
    def from_yaml(cls, d):
        candidates = []
        for entry in d:
            candidates.append(Candidate.from_yaml(entry))
        return cls(candidates=candidates)

    @classmethod
    def from_path(cls, path):
        with open(path, 'r') as f:
            return cls.from_yaml(yaml.full_load(f))
