#!/usr/bin/python

from setuptools import setup

setup(
      name='silver-platter',
      author="Jelmer Vernooij",
      author_email="jelmer@jelmer.uk",
      url="https://jelmer.uk/code/silver-platter",
      description="Automatic merge version control updater",
      version='0.0.1',
      license='Apachev2',
      project_urls={
          "Bug Tracker": "https://github.com/jelmer/silver-platter/issues",
          "Repository": "https://jelmer.uk/code/silver-platter",
          "GitHub": "https://github.com/jelmer/silver-platter",
      },
      keywords="git bzr vcs github gitlab launchpad",
      packages=[],
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
