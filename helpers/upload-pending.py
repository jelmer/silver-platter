#!/usr/bin/python3

import argparse
import psycopg2

from silver_platter.debian import (
    get_source_package,
    Workspace,
    DEFAULT_BUILDER,
    )
from silver_platter.debian.uploader import (
    dput_changes,
    prepare_upload_package,
    )
from silver_platter.utils import (
    open_branch,
    BranchUnavailable,
    BranchMissing,
    BranchUnsupported,
    )


parser = argparse.ArgumentParser()
parser.add_argument(
    '--maintainer-email', type=str,
    help='Maintainer to find unuploaded packages for.')
parser.add_argument(
    '--limit', type=int, default=1,
    help='Number of packages to upload.')
parser.add_argument(
    '--min-commit-age',
    help='Minimum age of the last commit, in days',
    type=int, default=0)
parser.add_argument(
    '--builder',
    type=str,
    help='Build command',
    default=(DEFAULT_BUILDER + ' --source --source-only-changes '
             '--debbuildopt=-v${LAST_VERSION}'))
parser.add_argument(
    '--dry-run', action='store_true',
    help='Dry run changes.')

args = parser.parse_args()

conn = psycopg2.connect(
    database="udd",
    user="udd-mirror",
    password="udd-mirror",
    host="udd-mirror.debian.net")
cursor = conn.cursor()
cursor.execute("""
SELECT sources.source, vcswatch.url
FROM vcswatch JOIN sources ON sources.source = vcswatch.source
WHERE
 vcswatch.status IN ('COMMITS', 'NEW') AND
 sources.release = 'sid' AND
sources.maintainer_email = %s
LIMIT %s
""", (args.maintainer_email, args.limit))

packages = list(cursor.fetchall())
print(packages)
for package, vcs_url in packages:
    pkg_source = get_source_package(package)
    try:
        main_branch = open_branch(vcs_url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        show_error('%s: %s', vcs_url, e)
        ret = 1
        continue
    with Workspace(main_branch) as ws:
        try:
            target_changes = prepare_upload_package(
                ws.local_tree, '',
                pkg_source["Package"], pkg_source["Version"],
                min_commit_age=args.min_commit_age, builder=args.builder)
        except Exception as e:
            print(e)
            continue

        ws.push(dry_run=args.dry_run)
        if not args.dry_run:
            dput_changes(target_changes)
