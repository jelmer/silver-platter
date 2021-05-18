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

Silver-Platter powers the Debian Janitor (https://janitor.debian.org/) and
Kali Janitor (https://kali.janitor.org/). The UI is still a bit rough around
the edges, I'd be grateful for any feedback from people using it.

Getting started
~~~~~~~~~~~~~~~

To log in to a code-hosting site, use ``svp login``::

    svp login https://github.com/

The simplest way to create a change as a merge proposal is to run something like::

    svp run ----mode=propose https://github.com/jelmer/dulwich ./framwork.sh

where ``framwork.sh`` makes some modifications to a working copy and prints the
commit message and body for the pull request to standard out. For example::

    #!/bin/sh
    sed -i 's/framwork/framework/' README.rst
    echo "Fix common typo: framwork => framework"

Recipes
~~~~~~~

To make this process a little bit easier to repeat, recipe files can be used.
For this example, create one called ``framwork.yaml`` with the following contents::

    ---
    name: framwork
    command: ./framwork.sh
    mode: propose
    merge-request:
      commit-message: Fix a typo
      description:
        markdown: |-
          I spotted that we commonly mistype *framework* as *framwork*.

To execute this recipe, run::

    svp run --recipe=framwork.yaml https://github.com/jelmer/dulwich

See `example.yaml` for an example recipe with plenty of comments

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

* *upload-pending*: Build and upload a package and push/propose the
  changelog updates.
* *run*: Similar to *svp run* but ensures that the *upstream* and *pristine-tar*
  branches are available as well, and can test that the branch still
  builds.

Some Debian-specific example recipes are provided in examples/debian/:

* *lintian-fixes.yaml*: Run the `lintian-brush
  <https://packages.debian.org/lintian-brush>`_ command to
  fix common issues reported by `lintian
  <https://salsa.debian.org/qa/lintian>`_.
* *new-upstream-release.yaml*: Merge in a new upstream release.
* *multi-arch-hints.yaml*: Apply multi-arch hints.
* *orphan.yaml*: Mark a package as orphaned, update its Maintainer
  field and move it to the common Debian salsa group.
* *rules-requires-root.yaml*: Mark a package as "Rules-Requires-Root: no"

*debian-svp run* takes package name arguments that will be resolved
to repository locations from the *Vcs-Git* field in the package.

See ``debian-svp COMMAND --help`` for more details.

Examples running ``debian-svp``::

    debian-svp run --recipe=examples/lintian-brush.yaml samba

    debian-svp upload-pending tdb
    debian-svp upload-pending --vcswatch --maintainer jelmer@debian.org

    debian-svp run --recipe=examples/new-upstream-release.yaml \
        --no-build-verify tdb

    debian-svp run --recipe=examples/multiarch-hints.yaml tdb

Credentials
~~~~~~~~~~~

The ``svp hosters`` subcommand can be used to display the hosting sites that
silver-platter is aware of::

    svp hosters

And to log into a new hosting site, simply run ``svp login BASE-URL``, e.g.::

    svp login https://launchpad.net/
