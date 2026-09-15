#!/usr/bin/env python3
"""Remove the stored documents of runs that are finished with.

Every sweep writes pages to the storage root and nothing ever removes them, so
on a box with one disk the root grows until the disk is the limit. Two kinds of
thing accumulate there and they are not the same kind:

- A run's own pages, under a directory named by the run id. Nothing outside
  that run reads them once its figures are reviewed.
- The document cache, keyed by the accession rather than by the run, so two
  products citing one filing read the same bytes. Deleting it costs a refetch,
  not an answer.

Which prefixes a run directory appears under is decided by the connectors that
write the keys, not here: this walks the root and takes any directory whose
name is a run id, so a connector that starts writing under a new prefix is
covered without this script changing. A run id is what ``new_id`` returns,
which is ``str(uuid4())`` - that is the whole test for "is this a run".

Only directories are removed. The database and its write-ahead log sit as files
at the root and can never match.

Stdlib only, and no import of the application: this runs on the deployment host
from the system python, against the directory the container mounts, while the
container is up.

    python3 scripts/prune_storage.py deploy/storage --older-than 30
    python3 scripts/prune_storage.py deploy/storage --older-than 30 --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from uuid import UUID

# Where the shared document cache lives under the storage root. A snapshot of
# the prefix SecConnector._cache_key builds (backend/app/connectors/sources.py),
# and stale if that changes - unlike the run directories below, a cache key
# carries no run id, so there is nothing in the name to derive it from.
CACHE_PREFIX = "cache"


def _is_run_id(name: str) -> bool:
    """Whether a directory name is one of ``new_id``'s ids."""
    try:
        UUID(name)
    except ValueError:
        return False
    return True


def _newest_mtime(tree: Path) -> float:
    """The most recent write anywhere under a directory, including its own.

    A run whose export was regenerated last week is not an old run, even
    though the sweep that made it was months ago.
    """
    newest = tree.stat().st_mtime
    for item in tree.rglob("*"):
        try:
            newest = max(newest, item.stat().st_mtime)
        except OSError:
            continue
    return newest


def _size(tree: Path) -> int:
    total = 0
    for item in tree.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def run_directories(root: Path) -> list[Path]:
    """Every run directory under the root, whatever prefix it was written to."""
    found = []
    for prefix in sorted(p for p in root.iterdir() if p.is_dir()):
        found.extend(sorted(c for c in prefix.iterdir() if c.is_dir() and _is_run_id(c.name)))
    return found


def cache_directories(root: Path) -> list[Path]:
    """The per-document directories of the shared cache, if it is there."""
    cache = root / CACHE_PREFIX
    if not cache.is_dir():
        return []
    # cache/<source>/<document key>/ - the leaf is one fetched document.
    return sorted(d for source in cache.iterdir() if source.is_dir() for d in source.iterdir() if d.is_dir())


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"


def prune(candidates: list[Path], *, cutoff: float, root: Path, apply: bool) -> int:
    freed = 0
    for tree in candidates:
        if _newest_mtime(tree) >= cutoff:
            continue
        size = _size(tree)
        freed += size
        age_days = (time.time() - _newest_mtime(tree)) / 86400
        print(f"  {'removing' if apply else 'would remove'} {tree.relative_to(root)}"
              f"  {_human(size)}  {age_days:.0f}d")
        if apply:
            shutil.rmtree(tree)
    return freed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="the storage root, e.g. deploy/storage")
    parser.add_argument("--older-than", type=float, default=30, metavar="DAYS",
                        help="prune runs untouched for this many days (default: 30)")
    parser.add_argument("--cache-older-than", type=float, default=None, metavar="DAYS",
                        help="also prune cached documents untouched for this many days; "
                             "they are refetched on demand")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without it nothing is removed")
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        parser.error(f"{root} is not a directory")

    freed = 0
    cutoff = time.time() - args.older_than * 86400
    runs = run_directories(root)
    print(f"run directories: {len(runs)} stored, untouched for more than {args.older_than:g}d:")
    freed += prune(runs, cutoff=cutoff, root=root, apply=args.apply)

    if args.cache_older_than is not None:
        cached = cache_directories(root)
        cache_cutoff = time.time() - args.cache_older_than * 86400
        print(f"cached documents: {len(cached)} stored, "
              f"untouched for more than {args.cache_older_than:g}d:")
        freed += prune(cached, cutoff=cache_cutoff, root=root, apply=args.apply)

    print(f"\n{'freed' if args.apply else 'would free'} {_human(freed)}")
    if not args.apply and freed:
        print("re-run with --apply to remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
