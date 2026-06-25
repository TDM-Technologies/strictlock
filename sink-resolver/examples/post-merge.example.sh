#!/bin/sh
# post-merge — keep a generated sink fresh after a CLEAN merge (the non-conflicting case).
#
# Install: copy to .git/hooks/post-merge and `chmod +x`, OR (preferred — version-controlled
# and shared) commit it under .githooks/ and run once:
#     git config core.hooksPath .githooks
#
# What it does: a clean merge brings in new/changed SOURCE files but does NOT re-run the
# generator, so the committed sink can go stale. This hook regenerates it so the derived
# surface stays honest with zero manual steps.
#
# Safe by construction: post-merge runs AFTER the merge has completed, so it can never block
# or fail a merge — it only ever regenerates a derived file. The CONFLICTING case (both sides
# changed the sink -> merge=binary leaves it unmerged) is the OTHER path: a conflicted merge
# stops before post-merge ever runs, and is handled by `sink-resolver.py resolve`.
#
# Configure SINK_RESOLVER_* in the environment this hook runs in (see ../CONFIG.md). This
# example regenerates in place and re-stages the sink only if the working tree merge left it
# changed; it does not create a commit (post-merge runs after the merge commit already exists,
# so a regenerated sink rides the NEXT commit — or wire a `--amend` in if your flow prefers).

set -eu

root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
gen="${SINK_RESOLVER_GENERATOR_CMD:-}"
[ -n "$gen" ] || exit 0          # not configured here -> nothing to do

# Regenerate in place. A generator failure must not break the merge that already happened,
# so we report and exit 0 (the commit/push freshness gates are the fail-closed backstops).
( cd "$root" && eval "$gen" ) || {
    echo "post-merge: generator failed; sink may be stale — run sink-resolver.py check in CI." >&2
    exit 0
}
exit 0
