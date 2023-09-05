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

from contextlib import suppress
from typing import List, Optional, Type

from breezy.bzr import RemoteBzrProber
from breezy.controldir import ControlDirFormat, Prober
from breezy.errors import UnsupportedFormatError
from breezy.git import RemoteGitProber


class UnsupportedVCSProber(Prober):
    def __init__(self, vcs_type) -> None:
        self.vcs_type = vcs_type

    def __eq__(self, other):
        return (isinstance(other, type(self))
                and other.vcs_type == self.vcs_type)

    def __call__(self):
        # The prober expects to be registered as a class.
        return self

    def priority(self, transport):
        return 200

    def probe_transport(self, transport):
        raise UnsupportedFormatError(
            "This VCS %s is not currently supported." % self.vcs_type
        )

    @classmethod
    def known_formats(klass):
        return []


prober_registry = {
    "bzr": RemoteBzrProber,
    "git": RemoteGitProber,
}

try:
    from breezy.plugins.fossil import RemoteFossilProber
except ImportError:
    pass
else:
    prober_registry["fossil"] = RemoteFossilProber

try:
    from breezy.plugins.svn import SvnRepositoryProber
except ImportError:
    pass
else:
    prober_registry["svn"] = SvnRepositoryProber

try:
    from breezy.plugins.hg import SmartHgProber
except ImportError:
    pass
else:
    prober_registry["hg"] = SmartHgProber

try:
    from breezy.plugins.darcs import DarcsProber
except ImportError:
    pass
else:
    prober_registry["darcs"] = DarcsProber

try:
    from breezy.plugins.cvs import CVSProber
except ImportError:
    pass
else:
    prober_registry["cvs"] = CVSProber


def select_probers(vcs_type=None):
    if vcs_type is None:
        return None
    try:
        return [prober_registry[vcs_type.lower()]]
    except KeyError:
        return [UnsupportedVCSProber(vcs_type)]


def select_preferred_probers(
        vcs_type: Optional[str] = None) -> List[Type[Prober]]:
    probers = list(ControlDirFormat.all_probers())
    if vcs_type:
        with suppress(KeyError):
            probers.insert(0, prober_registry[vcs_type.lower()])
    return probers
