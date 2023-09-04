Silver-Platter
==============

Silver-Platter makes it possible to contribute automatable changes to source
code in a version control system
(`codemods <https://github.com/jelmer/awesome-codemods>`_).

It automatically creates a local checkout of a remote repository,
makes user-specified changes, publishes those changes on the remote hosting
site and then creates a pull request.

In addition to that, it can also perform basic maintenance on branches
that have been proposed for merging - such as restarting them if they
have conflicts due to upstream changes.

Silver-Platter powers the Debian Janitor (https://janitor.debian.org/) and
Kali Janitor (https://kali.janitor.org/). However, it is an independent project
and can be used fine as a standalone tool. The UI is still a bit rough around
the edges, I'd be grateful for any feedback from people using it - please file bugs in
the issue tracker at https://github.com/jelmer/silver-platter/issues/new.

Getting started
~~~~~~~~~~~~~~~

To log in to a code-hosting site, use ``svp login``::

    svp login https://github.com/

The simplest way to create a change as a merge proposal is to run something like::

    svp run --mode=propose ./framwork.sh https://github.com/jelmer/dulwich

where ``framwork.sh`` makes some modifications to a working copy and prints the
commit message and body for the pull request to standard out. For example::

    #!/bin/sh
    sed -i 's/framwork/framework/' README.rst
    echo "Fix common typo: framwork ⇒ framework"

If you leave pending changes, silver-platter will automatically create a commit
and use the output from the script as the commit message. Scripts also
create their own commits if they prefer - this is especially useful if they
would like to create multiple commits.

Recipes
~~~~~~~

To make this process a little bit easier to repeat, recipe files can be used.
For the example above, we could create a ``framwork.yaml`` with the following
contents::

    ---
    name: framwork
    command: |-
     sed -i 's/framwork/framework/' README.rst
     echo "Fix common typo: framwork ⇒ framework"
    mode: propose
    merge-request:
      commit-message: Fix a typo
      description:
        markdown: |-
          I spotted that we often mistype *framework* as *framwork*.

To execute this recipe, run::

    svp run --recipe=framwork.yaml https://github.com/jelmer/dulwich

See `example.yaml` for an example recipe with plenty of comments.

In addition, you can run a particular recipe over a set of repositories by
specifying a candidate list.
For example, if *candidates.yaml* looked like this::

   ---
   - url: https://github.com/dulwich/dulwich
   - url: https://github.com/jelmer/xandikos

then the following command would process each repository in turn::

    svp run --recipe=framwork.yaml --candidates=candidates.yaml

Batch Mode
~~~~~~~~~~

Use batch mode when you're going to make a large number of changes and would
like to review or modify the diffs before sending them out::

    svp batch generate --recipe=framwork.yaml --candidates=candidate.syml framwork

This will then create a directory called "framwork", with a file called
``batch.yaml`` with all the pending changes::

    name: framwork
    work:
    - url: https://github.com/dulwich/dulwich
      name: dulwich
      description: I spotted that we often mistype *framework* as *framwork*.
      commit-message: Fix a typo
      mode: propose
    - url: https://github.com/jelmer/xandikos
      name: dulwich
      description: I spotted that we often mistype *framework* as *framwork*.
      commit-message: Fix a typo
      mode: propose
    recipe: ../framwork.yaml

For each of the candidates, a clone with the changes is created. You can introspect
and modify the clones as appropriate.

After you review the changes, edit batch.yaml as you see fit - remove
entries that don't appear to be correct, edit the details for the merge
requests, etc.

Once you're happy, you can publish the results::

    svp batch publish framwork

This will publish all the changes, using the mode and parameters specified in
``batch.yaml``.

``batch.yaml`` is automatically stripped of any entries in work that have fully
landed, i.e. where the pull request has been merged or where the changes were
pushed to the origin.

To check up on the status of your changes, run ``svp batch status``::

    svp batch status framwork

And to refresh any merge proposals that may have become out of date,
run publish again::

    svp batch publish framwork

Supported hosters
~~~~~~~~~~~~~~~~~

At the moment, the following code hosters are supported:

* `GitHub <https://github.com/>`_
* `Launchpad <https://launchpad.net/>`_
* `GitLab <https://gitlab.com/>`_ instances, such as Debian's
  `Salsa <https://salsa.debian.org>`_ or `GNOME's GitLab <https://gitlab.gnome.org/>`_

Working with Debian packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Several common operations for Debian packages have dedicated subcommands
under the ``debian-svp`` command. These will also automatically look up
packaging repository location for any Debian package names that are
specified.

* *upload-pending*: Build and upload a package and push/propose the
  changelog updates.
* *run*: Similar to *svp run* but specific to Debian packages:
  it ensures that the *upstream* and *pristine-tar* branches are available as
  well, can optionally update the changelog, and can test that the branch still
  builds.

Some Debian-specific example recipes are provided in `examples/debian/`:

* *lintian-fixes.yaml*: Run the `lintian-brush
  <https://packages.debian.org/lintian-brush>`_ command to
  fix common issues reported by `lintian
  <https://salsa.debian.org/qa/lintian>`_.
* *new-upstream-release.yaml*: Merge in a new upstream release.
* *multi-arch-hints.yaml*: Apply multi-arch hints.
* *orphan.yaml*: Mark a package as orphaned, update its Maintainer
  field and move it to the common Debian salsa group.
* *rules-requires-root.yaml*: Mark a package as "Rules-Requires-Root: no"
* *cme.yaml*: Run "cme fix dpkg", from the
  `cme package <https://packages.debian.org/cme>`_.

*debian-svp run* takes package name arguments that will be resolved
to repository locations from the *Vcs-Git* field in the package.

See ``debian-svp COMMAND --help`` for more details.

Examples running ``debian-svp``::

    # Create merge proposal running lintian-brush against Samba
    debian-svp run --recipe=examples/lintian-brush.yaml samba

    # Upload pending changes for tdb
    debian-svp upload-pending tdb

    # Upload pending changes for any packages maintained by Jelmer,
    # querying vcswatch.
    debian-svp upload-pending --vcswatch --maintainer jelmer@debian.org

    # Import the latest upstream release for tdb, without testing
    # the build afterwards.
    debian-svp run --recipe=examples/debian/new-upstream-release.yaml \
        --no-build-verify tdb

    # Apply multi-arch hints to tdb
    debian-svp run --recipe=examples/debian/multiarch-hints.yaml tdb

The following environment variables are provided for Debian packages:

* ``DEB_SOURCE``: the source package name
* ``DEB_UPDATE_CHANGELOG``: indicates whether a changelog entry should
  be added. Either "leave" (leave alone) or "update" (update changelog).

Credentials
~~~~~~~~~~~

The ``svp hosters`` subcommand can be used to display the hosting sites that
silver-platter is aware of::

    svp hosters

And to log into a new hosting site, simply run ``svp login BASE-URL``, e.g.::

    svp login https://launchpad.net/

Exit status
~~~~~~~~~~~

``svp run`` will exit 0 if no changes have been made, 1 if at least one
repository has been changed and 2 in case of trouble.

Python API
~~~~~~~~~~

Other than the command-line API, silver-platter also has a Python API.
The core class is the ``Workspace`` context manager, which exists in two forms:

 * ``silver_platter.workspace.Workspace`` (for generic projects)
 * ``silver_platter.debian.Workspace`` (for Debian packages)

An example, adding a new entry to a changelog file in the ``dulwich`` Debian
package and creating a merge proposal with that change::

    from silver_platter.debian import Workspace
    import subprocess

    with Workspace.from_apt_package(package="dulwich") as ws:
        subprocess.check_call(['dch', 'some change'], cwd=ws.path)
        ws.commit()  # Behaves like debcommit
        ws.publish(mode='propose')
