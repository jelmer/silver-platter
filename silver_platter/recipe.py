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
from jinja2 import Template
from typing import Optional, Dict, Union, List
import yaml


@dataclass
class Recipe(object):
    """Recipe to use."""

    name: str
    command: Union[str, List[str]]
    merge_request_description_template: Dict[Optional[str], Template]
    merge_request_commit_message_template: Template
    resume: bool = False
    commit_pending: Optional[bool] = True
    propose_threshold: Optional[int] = None

    @classmethod
    def from_yaml(cls, d):
        merge_request = d.get('merge-request', {})
        if merge_request:
            description = merge_request.get('description', {})
            if isinstance(description, dict):
                merge_request_description_template = description
            else:
                merge_request_description_template = {None: description}
            merge_request_commit_message_template = merge_request.get('commit-message')
            propose_threshold = merge_request.get('propose-threshold')
        else:
            merge_request_description_template = {}
            merge_request_commit_message_template = None
            propose_threshold = None
        return cls(
            name=d.get('name'),
            command=d.get('command'),
            resume=d.get('resume', False),
            commit_pending=d.get('commit-pending'),
            merge_request_description_template=merge_request_description_template,
            merge_request_commit_message_template=merge_request_commit_message_template,
            propose_threshold=propose_threshold)

    def render_merge_request_commit_message(self, context):
        template = self.merge_request_commit_message_template
        if template:
            return Template(template).render(context)
        return None

    def render_merge_request_description(self, description_format, context):
        template = self.merge_request_description_template.get(description_format)
        if template is None:
            try:
                template = self.merge_request_description_template[None]
            except KeyError:
                return None
        return Template(template).render(context)

    @classmethod
    def from_path(cls, path):
        with open(path, 'r') as f:
            return cls.from_yaml(yaml.full_load(f))
