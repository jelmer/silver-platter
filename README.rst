Silver-Platter
==============

Silver-Platter makes it possible to contribute automatable changes to source
code in a version control system.

It automatically creates a local checkout of a remote repository,
makes user-specified changes, publishes those changes on the remote hosting
site and then creates a pull request.

In addition to that, it can also perform basic maintenance on branches
that have been proposed for merging - such as restarting them if they
have conflicts due to upstream changes.

Getting started
~~~~~~~~~~~~~~~

To log in to a code-hosting site, use ``svp login``::

    svp login https://github.com/

The simplest way to create a change as a merge proposal is to run something like::

    svp run --mode=propose https://github.com/jelmer/dulwich ./some-script.sh

where ``some-script.sh`` makes some modifications to a working copy and prints the
body for the pull request to standard out. For example::

    #!/bin/sh
    sed -i 's/framwork/framework/' README.rst
    echo "Fix common typo: framwork => framework"

Supported hosters
~~~~~~~~~~~~~~~~~

At the moment, the following code hosters are supported:

 * `GitHub <https://github.com/>`_
 * `Launchpad <https://launchpad.net/>`_
 * `GitLab <https://gitlab.com/>`_ instances, such as Debian's
   `Salsa <https://salsa.debian.org>`_

Working with Debian packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Several common operations for Debian packages have dedicated subcommands
under the ``debian-svp`` command. These will also automatically look up
packaging repository location for any Debian package names that are
specified.

Subcommands that are available include:

 * *lintian-brush*: Run the `lintian-brush
   <https://packages.debian.org/lintian-brush>`_ command on the branch.
 * *upload-pending*: Build and upload a package and push/propose the
   changelog updates.
 * *new-upstream*: Merge in a new upstream release or snapshot.
 * *apply-multi-arch-hints*: Apply multi-arch hints.
 * *orphan*: Mark a package as orphaned, update its Maintainer
   field and and move it to the common Debian salsa group.
 * *rules-requires-root*: Mark a package as "Rules-Requires-Root: no"

*debian-svp run* takes package name arguments that will be resolved
to repository locations from the *Vcs-Git* field in the package.

See ``debian-svp COMMAND --help`` for more details.

Examples running ``debian-svp``::

    debian-svp lintian-brush samba
    debian-svp lintian-brush --mode=propose samba
    debian-svp lintian-brush --mode=push samba

    debian-svp upload-pending tdb

    debian-svp new-upstream --no-build-verify tdb

    debian-svp apply-multi-arch-hints tdb

Credentials
~~~~~~~~~~~

The ``svp hosters`` subcommand can be used to display the hosting sites that
silver-platter is aware of::

    svp hosters

And to log into a new hosting site, simply run ``svp login BASE-URL``, e.g.::

    svp login https://launchpad.net/
