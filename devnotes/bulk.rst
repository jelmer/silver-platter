Commands:

svp bulk generate --recipe=foo.yml --candidates=bar.yml --out=directory

This then generates changes in directory for each of the candidates.
Per candidate, we'll need:

* patch
* commit message
* mode + details
* some way of updating, e.g. reference to recipe

svp bulk apply directory

Iterates over all entries in directory and makes the requisite changes.

Stores metadata about each applied change in a JSON file.

svp bulk refresh directory

Iterates over all entries in directory and updates.
