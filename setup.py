#!/usr/bin/python3
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

debian_deps = [
    'pyyaml',
    'debmutate>=0.3',
    'python_debian',
    'distro-info',
]


setup(
    name='silver-platter',
    author="Jelmer Vernooij",
    author_email="jelmer@jelmer.uk",
    url="https://jelmer.uk/code/silver-platter",
    description="Automatic merge proposal creeator",
    version='0.4.6',
    license='GNU GPL v2 or later',
    project_urls={
        "Bug Tracker": "https://github.com/jelmer/silver-platter/issues",
        "Repository": "https://jelmer.uk/code/silver-platter",
        "GitHub": "https://github.com/jelmer/silver-platter",
    },
    keywords="git bzr vcs github gitlab launchpad",
    packages=[
        'silver_platter',
        'silver_platter.debian',
        'silver_platter.tests',
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Operating System :: POSIX',
        'Topic :: Software Development :: Version Control',
    ],
    entry_points={
        'console_scripts': [
            'svp=silver_platter.__main__:main',
            'debian-svp=silver_platter.debian.__main__:main',
        ],
    },
    test_suite='silver_platter.tests.test_suite',
    install_requires=[
        'breezy>=3.2.0',
        'dulwich>=0.20.23',
        'jinja2',
    ],
    extras_require={
        'debian': debian_deps,
    },
    tests_require=['testtools'] + debian_deps,
)
