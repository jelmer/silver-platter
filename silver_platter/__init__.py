#!/usr/bin/python
# Copyright (C) 2018 Jelmer Vernooij
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

# TODO(jelmer): Imports with side-effects are bad...

import breezy  # noqa: F401
import breezy.bzr  # For bzr support   # noqa: F401
import breezy.git  # For git support   # noqa: F401
import breezy.plugins.github  # For github support  # noqa: F401
import breezy.plugins.gitlab  # For gitlab support  # noqa: F401
import breezy.plugins.launchpad  # For lp: URL support  # noqa: F401

__version__ = (0, 5, 9)
version_string = ".".join(map(str, __version__))
