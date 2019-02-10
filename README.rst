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

    svp login https://github.com/

    svp autopropose https://github.com/jelmer/dulwich ./some-script.py

At the moment, the following code hosters are supported:

 * `GitHub <https://github.com/>`_
 * `Launchpad <https://launchpad.net/>`_
 * `GitLab <https://gitlab.com/>`_ instances, such as Debian's
   `Salsa <https://salsa.debian.org>`_

Getting started
~~~~~~~~~~~~~~~

Working with Debian packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Several common operations for Debian packages have dedicated subcommands
in silver-platter.

See ``debian-svp COMMAND --help`` for more details.

Examples::

    debian-svp lintian-brush samba
    debian-svp lintian-brush --mode=propose samba
    debian-svp lintian-brush --mode=push samba

    debian-svp upload-pending tdb

    debian-svp merge-upstream --no-build-verify tdb

Credentials
~~~~~~~~~~~

The ``svp hosters`` subcommand can be used to display the hosting sites that
silver-platter is aware of::

    svp hosters

And to log into a new hosting site, simply run ``svp login BASE-URL``, e.g.::

    svp login https://launchpad.net/
