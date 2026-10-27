# spill-gate

**Refuse to commit or push a secret, a stray UUID, or a machine path.**

A [StrictLock](../README.md) module. Private keys, API tokens, UUIDs and home-directory paths
(a macOS, Windows or Linux path with a user name in it) are easy to paste into a commit and hard
to take back once they are in a public history. StrictLock's CI already greps for them after the
fact (the `secret-scan` job). `spill-gate` moves the same check to the **boundary**: a git
`pre-commit` hook scans what is staged, a `pre-push` hook scans the commits about to leave the
machine, and either one **refuses** on a hit. Fail closed: a git failure, a malformed allowlist or
any internal error blocks rather than passes.

## The honest limit

A regex gate **reduces** spill risk; it does **not eliminate** it. It recognises the *shape* of
keys, tokens, UUIDs and machine paths. It cannot tell that a paragraph is strategy, a person's
private detail, or an unreleased plan: "is this content private?" stays a human judgment call.
Treat a pass as "nothing mechanical matched", never as "this is safe to publish". Git also honors
`--no-verify`, so a client-side hook is a seatbelt, not a lock; CI (`secret-scan`) is the layer
behind it.

## What it flags

| Rule | Matches |
|---|---|
| `private-key` | `BEGIN [RSA \|EC \|OPENSSH \|PGP \|DSA \|ENCRYPTED ]PRIVATE KEY` armor lines |
| `aws-access-key` | `AKIA` / `ASIA` + 16 upper-case alphanumerics |
| `github-token` | `ghp_` `gho_` `ghu_` `ghs_` `ghr_` + 36 or more characters, or `github_pat_...` |
| `slack-token` | `xoxb-` `xoxa-` `xoxp-` `xoxr-` `xoxs-` + 10 or more characters |
| `api-key-sk` | `sk-ant-...` / `sk-proj-...` with 20 or more characters |
| `stripe-live-key` | `sk_live_` / `rk_live_` + 20 or more characters |
| `google-api-key` | `AIza` + 35 characters |
| `uuid` | any canonical 8-4-4-4-12 hex UUID (parity with CI: every UUID is suspect until allowlisted) |
| `machine-path` | `/Users/<name>` (name starts with a letter), `<Drive>:\Users[\<name>]`, `<Drive>:/Users[/<name>]`, `/home/<name>/`; the whole user segment is matched |

The five CI patterns (private key, AWS key id, GitHub token, UUID, machine path) are all covered,
with no leading word boundary, so a token glued to an identifier is still caught (a test asserts
this for every CI pattern); the rest are prefixed, length-bounded token shapes chosen to stay quiet on ordinary text.
`/home/<name>/` is new relative to CI and is the noisiest rule: documentation often uses
placeholder home paths (see "Allowlist"); the `literal` entry exists for those.

The report prints `path:line`, the rule and the first four characters of the match, never the
secret itself; the audit log does the same.

## Modes

```bash
python3 spill-gate.py pre-commit            # scan the git INDEX (staged additions)
python3 spill-gate.py pre-push REMOTE URL   # scan commits being pushed (reads git's stdin)
python3 spill-gate.py scan-tree [--rev REV] # scan every tracked file of REV (default HEAD)
```

- **pre-commit** scans the **added lines of the index** (`git diff --cached`), not the working
  tree: an unstaged edit is invisible to it, and a spill that is staged but already "fixed" in your
  editor still blocks. Renames are scanned as additions, so moving a file cannot dodge a path rule.
  Removing an existing spill is never blocked.
- **pre-push** reads the `<ref> <local-sha> <ref> <remote-sha>` lines git sends on stdin and
  scans **each commit** in `remote-sha..local-sha` separately, so a secret added in one commit and
  deleted in the next is still caught (it is still in the pushed history). For a **new branch**
  (remote sha all zeros) the range is every commit not reachable from any ref of that remote
  (`--not --remotes=<remote>`); if the remote has no refs yet, that is the whole history. Branch
  deletions push no content and pass. If the remote tip is unknown locally, it falls back to the
  new-branch rule rather than skipping.
- **scan-tree** reads whole files of a revision. It is the dogfood / audit mode and runs even when
  `SPILL_GATE` is off.

**What is scanned, honestly.** Every added line of every changed file is scanned as text. Git
attributes (`-diff`, `binary`) and git's binary heuristic do not exempt a file (the diff is forced
with `--text`). NUL bytes are dropped from a line before matching, so ASCII inside UTF-16/UTF-32
text and inside binary blobs is still found. What is *not* found: anything compressed, encrypted,
base64-wrapped or otherwise encoded (a token in a zip, a packed image, a base64 blob), and text in
other legacy encodings whose bytes differ from ASCII. Binary files therefore need an allowlist
entry if they legitimately contain a match. Submodule entries (gitlinks) carry no content here and
are skipped by `scan-tree`; scan the submodule's own repo. Symlinks are scanned as their target text;
typechanges (symlink to regular file) are scanned. Merge commits on push are scanned for what the
merge itself introduced (`--cc`); the commits it brought in are scanned as ordinary commits.

## Install

The mode is taken from the hook's file name, so a plain copy works:

```bash
cp spill-gate.py /path/to/your/repo/.git/hooks/pre-commit
cp spill-gate.py /path/to/your/repo/.git/hooks/pre-push
chmod +x /path/to/your/repo/.git/hooks/pre-commit /path/to/your/repo/.git/hooks/pre-push
```

Or keep one copy and use the wrappers in [`examples/`](examples/), which call it with an explicit
mode. Then switch it on with `SPILL_GATE=on` (e.g. exported in the wrapper). Without it the hooks
are inert (allow-all), like the other StrictLock gates. Requires Python 3.8+, standard library only;
works on Linux, macOS and Windows.

Hooks that already exist in the repo (for example `generated-sink-commit-gate`) can be chained from
one wrapper script: run each, stop on the first non-zero exit. For `pre-push`, git's stdin can be read
once, so a wrapper running several readers must save it first.

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `SPILL_GATE` | `on`, `1`, `true` or `yes` (any case) enables the hooks. Anything else: inert. |
| `SPILL_GATE_ALLOWLIST` | Explicit, trusted **override**: read this file from disk (absolute or repo-root-relative) instead of the allowlist stored in git. Must exist (a typo fails closed). Unset by default; see "Allowlist". |
| `SPILL_GATE_LOG_DIR` | Directory for an append-only JSON-lines log (`spill-gate.log`). Records decisions (`allow`, `block`, `bypass`), never matched text. |
| `SPILL_GATE_BYPASS` | `on`/`1`/`true`/`yes` skips the **pre-commit** gate for that invocation. Set it inline per commit. |
| `SPILL_GATE_PUSH_BYPASS` | Same, for the **pre-push** gate only. Distinct on purpose (as in `generated-sink-prepush-gate`): bypassing the commit gate does not bypass the push backstop. |

A bypass is always announced on stderr; it is written to the audit log (as decision `bypass`) only
when `SPILL_GATE_LOG_DIR` is set.

## Allowlist

Two mechanisms, for the cases where a hit is a deliberate, public-safe example.

**Per-line marker.** Put `spill-gate: allow` anywhere on the line (usually in a comment) to exempt
that line from every rule, or `spill-gate: allow=uuid,machine-path` to exempt it from named rules
only. The marker is exact: one space after the colon, a comma-separated rule list with no spaces,
and nothing glued on after it (`allowed`, `allow=uuid,` and `allow=` are not markers). An unknown
rule name in a marker blocks (fail-closed). The marker covers only its own line.

**Allowlist file** `.spill-gate-allow` at the repo root, one entry per line, `#` comments. The gate
reads it **from git, not from the working tree**: the staged copy for pre-commit, the pushed tip
commit's copy for pre-push, the scanned revision's copy for scan-tree. A file that exists only on
disk, or an unstaged edit, has no effect; if the file is absent from git there is no allowlist. A
commit that adds or widens the allowlist is judged by the allowlist it carries, so that change is
the one to review.

```text
# whole files or directories (gitignore-style globs; ** crosses '/', * does not)
path tests/fixtures/**
path **/*.sample
# the same, but only for named rules
path docs/examples/** rules=uuid,machine-path
# one exact matched text (or one exact whole line); the text may not contain a newline
literal <the exact text the gate matched, e.g. a placeholder home path>
```

A pattern containing no `/` matches the file name at any depth; a trailing `/` means everything
below. Path globs cannot contain spaces. An unknown rule name, an unparseable line or an unreadable
file blocks, so a typo can never quietly turn the gate off. A `literal` must equal the whole matched
text, and the machine-path rule matches the whole user segment (the whole name, not its first letter), so a
literal for one user never exempts a longer name. `SPILL_GATE_ALLOWLIST` replaces the git copy with
a file on disk; it is meant for a trusted environment (your own shell or CI config), not a default.

**Keeping the gate's own tests clean.** The shipped test suite builds every spill-shaped fixture
from string fragments at run time, so this module carries no literal secret, UUID or machine path
and needs no allowlist entry (and passes CI's `secret-scan`). Do the same in your own fixtures, or
put them under an allowlisted `path`.

## Tests

```bash
python3 spill-gate/tests/test_spill_gate.py
```

Real git repos in temp dirs, the gate run as a subprocess (the true hook contract). Covers every
rule, near-miss negatives, staged-vs-working-tree, the allowlist (marker, path, per-rule path,
literal, malformed, missing), bypass and logging, binary and deleted files, pre-push ranges
(clean, dirty, added-then-removed, new branch with and without remote refs, root commit, deletion,
garbage stdin), a real `git push` blocked by a hook, and a self-check that this module's own files
trip nothing.

## What it does not do

- It does not judge meaning (see "The honest limit").
- It does not scan commit messages, file names, or anything outside file content.
- It does not scan the working tree on pre-commit, or ignored/untracked files.
- It cannot stop `--no-verify`, or a clone with no hook installed; pair it with CI.
- Entropy-based detection is deliberately absent: it is noisy, and this gate prefers a few
  predictable shapes to a flood of false alarms.
