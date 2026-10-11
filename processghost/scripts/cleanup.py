"""Delete old run data. Dry run by default: nothing is removed without --yes.

Usage (from the processghost/ directory):
    python -m scripts.cleanup --older-than-days 30                    # list old runs
    python -m scripts.cleanup --older-than-days 30 --yes              # delete them
    python -m scripts.cleanup --older-than-days 7 --media-only --yes  # keep text outputs, drop video/audio/frames
    python -m scripts.cleanup --older-than-days 0 --include-cache --yes   # also clear the model-call cache

See docs/data-handling.md for what a run and the cache keep on disk.
"""
from __future__ import annotations

import argparse

from core.retention import apply_cleanup, default_dirs, plan_cleanup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--older-than-days", type=float, required=True)
    parser.add_argument("--media-only", action="store_true", help="remove only video, audio and frames from old runs")
    parser.add_argument("--include-cache", action="store_true", help="also remove cache entries at least that old")
    parser.add_argument("--yes", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args()

    runs_dir, cache_dir = default_dirs()
    actions = plan_cleanup(runs_dir, cache_dir, args.older_than_days, args.media_only, args.include_cache)
    for a in actions:
        print("%-6s %s (%s)" % (a.kind, a.path, a.reason))
    if not actions:
        print("nothing to remove")
        return
    if not args.yes:
        print("dry run: %d item(s) would be removed. Re-run with --yes to delete." % len(actions))
        return
    print("removed %d item(s)" % apply_cleanup(actions))


if __name__ == "__main__":
    main()
