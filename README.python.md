# Python bindings for Silver-Platter

Silver-Platter makes it possible to contribute automatable changes to source
code in a version control system
([codemods](https://github.com/jelmer/awesome-codemods)).

It automatically creates a local checkout of a remote repository,
makes user-specified changes, publishes those changes on the remote hosting
site and then creates a pull request.

In addition to that, it can also perform basic maintenance on branches
that have been proposed for merging - such as restarting them if they
have conflicts due to upstream changes.

This package contains Python bindings for silver platter.

## Usage

The core class is the ``Workspace`` context manager, which exists in two forms:

* ``silver_platter.workspace.Workspace`` (for generic projects)
* ``silver_platter.debian.Workspace`` (for Debian packages)

An example, adding a new entry to a changelog file in the ``dulwich`` Debian
package and creating a merge proposal with that change:

```python

from silver_platter.debian import Workspace
import subprocess

with Workspace.from_apt_package(package="dulwich") as ws:
    subprocess.check_call(['dch', 'some change'], cwd=ws.path)
    ws.commit()  # Behaves like debcommit
    ws.publish(mode='propose')
```
