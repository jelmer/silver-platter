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


import sqlite3
import time

from xdg.BaseDirectory import save_data_path

state_dir = save_data_path('silver-platter')
con = sqlite3.connect(os.path.join(state_dir, 'state.db'))


def store_run(vcs_url, command, merge_proposal_url):
    """Store a run.

    :param vcs_url: Upstream branch URL
    :param command: Command
    :param merge_proposal_url: Optional merge proposal URL
    """
    cur = con.cursor()
    cur.execute("REPLACE INTO branch (url) VALUES (?)", (vcs_url, ))
    branch_id = cur.lastrowid
    if merge_proposal_url:
        cur.execute(
            "REPLACE INTO merge_proposal (url, branch_id) VALUES (?, ?)",
            (merge_proposal_url, branch_id))
        merge_proposal_id = cur.lastrowid
    else:
        merge_proposal_id = None
    cur.execute(
        "INSERT INTO run (command, finish_time, branch_id, merge_proposal_id) "
        "VALUES (?, ?, ?, ?)", (
            ' '.join(command), time.time(), branch_id, merge_proposal_id, ))
    con.commit()
