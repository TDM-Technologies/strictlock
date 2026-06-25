# scope-lease — worked walkthrough

A start-to-finish run showing the two failures `scope-lease` closes. Run these from inside a
git repo (any repo with at least one commit). The lock store lives in that repo's `.git`.

## 0. Setup

```bash
export SCOPE_LEASE=on
export SCOPE_LEASE_PLANS_DIR="$(pwd)/examples/plans"   # the add-logout-button plan
```

## 1. First agent acquires — wins

```bash
python3 scope-lease.py acquire
# LEASED: lock 'add-logout-button' (owner <your-branch>) holds 3 scope(s) until 2026-...
```

Three `refs/locks/*` refs now exist — one per `allowed_paths` entry. You can see them:

```bash
git for-each-ref refs/locks/
```

## 2. A second agent claims an overlapping scope — DENY

Point a *second* lock id at one of the same paths (here via the standalone `paths` source, to
simulate another session):

```bash
python3 scope-lease.py acquire \
  --source paths --lock-id agent-2 \
  --paths src/auth/session.ts
# scope-lease: DENIED: scope 'src/auth/session.ts' is already leased by lock
#   'add-logout-button' (owner ...) until 2026-... first-mover wins; ... wrote nothing.
echo $?    # 5
```

The DENY **names the blocker** and writes nothing. First-mover wins. This is the
**stale-holder-write** failure prevented *before* it can happen — agent-2 never gets a claim it
would later have to merge.

## 3. The same file under a different spelling — still DENY

The lock is on the *file*, not the *string*. An absolute path, a `./` prefix, or (on a
case-insensitive filesystem) a different casing all fold to the same key:

```bash
python3 scope-lease.py acquire \
  --source paths --lock-id agent-3 \
  --paths "$(pwd)/src/auth/session.ts"
echo $?    # 5 — same file, denied
```

## 4. Fence-check before merging — confirm you still hold it

Just before letting the first agent's work merge, confirm nobody reclaimed it:

```bash
python3 scope-lease.py fence-check
# FENCE OK: lock 'add-logout-button' still holds all 3 scope(s).
echo $?    # 0
```

If the first agent had run past its deadline and another agent had reclaimed the scope, this
would print `FENCE LOST` and exit `4` — the **deadlock-on-crash recovery** working: the scope
freed, someone else took it, and the stale holder is stopped at the gate instead of
silently clobbering the new holder's edits.

## 5. Release at close-out

```bash
python3 scope-lease.py release
# RELEASED: 3 scope(s) for lock 'add-logout-button'.
git for-each-ref refs/locks/    # empty again
```

Release is idempotent — running it twice is a clean no-op, and it never drops a lock another id
owns.

## The reclaim path (deadlock-on-crash), end to end

To see reclaim directly, give the first lease a tiny TTL, let it lapse, and acquire again:

```bash
python3 scope-lease.py acquire --ttl-seconds 1
sleep 2
python3 scope-lease.py acquire --source paths --lock-id rescuer --paths src/auth/session.ts
# scope-lease: RECLAIMED stale lock on 'src/auth/session.ts' (was lock 'add-logout-button',
#   owner ..., expired ...) -> token 2, new deadline ...
# LEASED: lock 'rescuer' (owner ...) holds 1 scope(s) until ...
```

The reclaim is **logged to stderr** — there is no background reaper. A scope only ever changes
hands as a visible side effect of someone actively acquiring it.
