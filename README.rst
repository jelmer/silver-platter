Silver-Platter
==============

Silver-Platter makes it possible to contribute automatable changes to source
code in a version control system.

It automatically creates a local checkout of a remote repository,
make user-specified changes, publish those changes on the remote hosting
site and then creates pull request.

In addition to that, it can also perform basic maintenance on branches
that have been proposed for merging - such as restarting them if they
have conflicts due to upstream changes.

In the simplest form, this could be running::

    svp autopropose https://github.com/jelmer/dulwich ./some-script.py

At the moment, the following code hosters are supported:

 * `GitHub <https://github.com/>`_
 * `Launchpad <https://launchpad.net/>`_
 * `GitLab <https://gitlab.com/>`_ instances, such as Debian's
   `Salsa <https://salsa.debian.org>`_

Working with Debian packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Several common operations for Debian packages have dedicated subcommands
in silver-platter.

See ``svp COMMAND --help`` for more details.

Examples::

    svp lintian-brush samba
    svp lintian-brush --mode=propose samba
    svp lintian-brush --mode=push samba

    svp upload-pending tdb

    svp merge-upstream --no-build-verify tdb

Credentials
~~~~~~~~~~~

Silver-Platter is built on top of `Breezy <https://www.breezy-vcs.org/>`_, and
uses Breezy for credential management.
