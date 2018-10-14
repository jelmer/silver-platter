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

from setuptools import setup

setup(
      name='silver-platter',
      author="Jelmer Vernooij",
      author_email="jelmer@jelmer.uk",
      url="https://jelmer.uk/code/silver-platter",
      description="Automatic merge version control updater",
      version='0.0.1',
      license='GPLv2',
      project_urls={
          "Bug Tracker": "https://github.com/jelmer/silver-platter/issues",
          "Repository": "https://jelmer.uk/code/silver-platter",
          "GitHub": "https://github.com/jelmer/silver-platter",
      },
      keywords="git bzr vcs github gitlab launchpad",
      packages=['silver_platter'],
      scripts=['autopropose.py', 'propose-lintian-fixes.py'],
      classifiers=[
          'Development Status :: 3 - Alpha',
          'License :: OSI Approved :: Apache Software License',
          'Programming Language :: Python :: 2.7',
          'Programming Language :: Python :: 3.3',
          'Programming Language :: Python :: 3.4',
          'Programming Language :: Python :: 3.5',
          'Programming Language :: Python :: 3.6',
          'Programming Language :: Python :: Implementation :: CPython',
          'Programming Language :: Python :: Implementation :: PyPy',
          'Operating System :: POSIX',
          'Topic :: Software Development :: Version Control',
      ],
      )
