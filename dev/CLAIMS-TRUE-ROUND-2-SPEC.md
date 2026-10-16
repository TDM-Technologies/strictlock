# Claims-true round 2 — spec (decided 2026-10-16)

**Status: DECIDED, not built.** The maintainer took the recommendation on all seven calls
(Q1–Q7) on 2026-10-16. That is dated direction and can be re-argued. See the decision log at the end.

**Where this comes from:** a newcomer-eye review of the public docs (2026-10-16, main @ `1cd53ca`)
found 18 problems. The maintainer ruled (2026-10-16) that all 18 go in, and that the overclaims
below become **true in code** rather than reworded. The other items are doc-only and need no design.
Same two-stage approach as round 1: the docs PR gives each fix target honest interim wording; the
code PR restores the stronger wording once a test backs it.

| # | Claim today | Made true by |
|---|---|---|
| 6, 7 | a plan is human-approved; COMPLIANCE row C: approval is "recorded" | §1 plan approval |
| 5 | MCP external-write calls are "checked against an explicitly approved plan" | §2 MCP path checks |
| 8 | scope-lease: N agents "never edit the same file at once" | §3 plan-gate honors leases |
| 12 | listing a state file in `allowed_paths` means only an approved session writes it | §4 writer scripts |
| 14 | bypass switches work as documented | §5 accept `on`/`1`/`true` |
| 4 | the ESLint legacy-config example works | §6 CommonJS entry |

---

## §1 Plan approval (#6, #7)

**Today (verified):** a plan is active when its frontmatter says `status: active`
(`find_active_plans`, plan-gate.py:1336). Anyone who can write the plans directory can activate a
plan. The example settings make the plans directory always-writable
(`examples/settings.json:8`), so a gated agent can write a new plan with `status: active` and it
takes effect at once. Nothing records who approved a plan or when.

**Design (recommended): hash-bound approval records.**
1. New CLI: `plan-gate.py approve <plan.md>`. It hashes the plan file's full bytes (sha256) and
   appends an `approve` row to the existing hash-chained decision log: plan name, plan content
   hash, timestamp, OS user, `git config user.name`/`user.email`, hostname.
2. A plan counts as active only if it says `status: active` **and** the log has an `approve` row
   whose content hash matches the file **as it is now**. Any edit after approval voids it, so an
   approved plan can't be widened later without re-approval. Deny message: "plan X is marked
   active but not approved (or changed since approval). Run: plan-gate.py approve X".
3. The agent can't approve its own plan:
   - plan-gate denies any gated Bash/PowerShell command that invokes `plan-gate.py approve`
     (same pattern as the existing log self-protection);
   - `approve` refuses to run without an interactive terminal (`stdin.isatty()`). Agent tool
     calls have none.
   - The log directory is already write-protected from gated agents (`log_self_protect`), so an
     agent can't forge an `approve` row.
4. Agents may keep drafting plans: the plans directory can stay writable, because a draft does
   nothing until a human approves it. That keeps the agent-drafts-human-approves workflow intact.
5. `plan-gate.py verify-log` also reports approve rows. COMPLIANCE row C is reworded to exactly
   this: who ran approve, when, and which plan bytes they approved.

**Honest scope (goes in the docs):** the approval identity comes from OS user and git config, which
the person themselves set. It shows approval happened outside the gated agent, at a recorded time,
for exact plan bytes, and the chain shows whether the record was altered. It is not
authentication: someone with a shell can approve as anyone they can configure. Same
tamper-evident-not-tamper-proof framing as the decision log.

**Requires:** `PLAN_GATE_LOG_DIR` set. Approval rows live in the log, so approval mode with no log
dir is a configuration error, and the gate denies with a clear message. This also fixes #11 for
anyone who turns approval on.

**Calls for the maintainer:**
- **Q1 — default.** (a) **Recommended:** approval required whenever `PLAN_GATE_LOG_DIR` is set, and
  the example settings set it. Off only via an explicit `PLAN_GATE_REQUIRE_APPROVAL=off`. (b)
  Opt-in `PLAN_GATE_REQUIRE_APPROVAL=on`, default off: no break for current users, but the default
  install keeps the self-approval hole. (c) Always required. Adopters on v0.x upgrade into denials
  until they approve.
- **Q2 — what the hash covers.** **Recommended:** the whole file. Alternative: frontmatter only,
  so body notes can change without re-approval, but then prose that guides the agent can change
  unreviewed.
- **Q3 — TTY requirement.** **Recommended:** yes, with `--no-tty-check` for CI/scripted approval.
  The flag is logged on the row, so it's visible.

## §2 MCP write tools — check paths against the plan (#5)

**Today (verified):** tools named in `PLAN_GATE_MCP_WRITE_TOOLS` are always denied while a plan is
active (plan-gate.py:2003-2011). Their arguments are never read. The example matcher
(`Edit|Write|NotebookEdit|Bash|PowerShell`) doesn't route MCP tools to the hook at all, so even
configured ones are never seen.

**Design (recommended):**
1. Entries can name the argument that holds the target path: `PLAN_GATE_MCP_WRITE_TOOLS=
   mcp__fs__write_file:path,mcp__fs__move:source+destination`. Several arguments are joined
   with `+`. If any named argument is a list of strings, every string is a target.
2. A configured tool **with** an argument spec: every target is checked exactly like an Edit/Write
   `file_path`: allowed_paths, always-writable, log self-protection, worktree claim. All must
   pass. A missing, empty or non-string argument means deny.
3. A configured tool **without** an argument spec: denied, as today (the safe default for a tool
   whose arguments we don't understand).
4. The example matcher becomes `Edit|Write|NotebookEdit|Bash|PowerShell|mcp__.*`, so MCP calls
   reach the gate. Unconfigured MCP tools are still allowed as today. Default-denying unknown
   tools is the separate, already-backlogged `allowed_tools:` item, and stays out of scope here.
5. Decision-log rows record the tool and each target, under the existing privacy caps.

**Calls:**
- **Q4 — matcher breadth.** **Recommended:** `mcp__.*`. The hook runs on every MCP call (a few
  ms each), and that buys a log row for each one. Alternative: list only the configured tools in
  the matcher. That's cheaper, but forgetting to update the matcher silently un-gates a tool,
  which is the very failure #5 describes.

## §3 plan-gate honors scope-lease locks (#8)

**Today (verified):** scope-lease locks are git refs `refs/locks/<sha1(canonical path)>`
(scope-lease.py:290). The path canonicalization folds case and Unicode based on the filesystem.
plan-gate never reads these refs, so a lease only excludes agents that themselves call `acquire`.

**Design (recommended):**
1. New read-only scope-lease command: `scope-lease.py holder <path>...`. Exit 0 = free or held by
   me (`SCOPE_LEASE_OWNER` / lock id), 5 = held by someone else (prints the holder), 2+ = error.
   Canonicalization stays in one place, scope-lease. plan-gate does not reimplement it, because a
   mismatch would silently miss locks.
2. plan-gate: when `PLAN_GATE_SCOPE_LEASE=/path/to/scope-lease.py` is set, every file-write target
   (Edit/Write/NotebookEdit, Bash redirect targets, §2 MCP targets, §4 writer targets) is checked
   with `holder` before the plan check. Held by another agent → deny, naming the holder and the
   lease's age. A holder error → deny (fail closed), with the bypass how-to.
3. Optional strict mode, `PLAN_GATE_REQUIRE_LEASE=on`: a write to a path with no lease is also
   denied, and the message says to acquire it first. This turns "agents never edit the same file
   at once" from cooperative into enforced for every gated agent.
4. Invariant kept: plan-gate only sees agent tool calls, so a human's manual edit or merge is
   never blocked (scope-lease's "never walls a human" rule).

**Calls:**
- **Q5 — another agent's lease.** **Recommended:** deny. Alternative: warn and allow, which keeps
  the lease advisory and leaves the claim false.
- **Q6 — strict mode default.** **Recommended:** opt-in. The README then states both levels:
  "leases are honored by every gated agent; with REQUIRE_LEASE, every gated write must hold one."

## §4 Writer scripts write where the plan allows (#12)

**Today (verified):** `python3 atomic-write.py STATE.md` is a Bash command. plan-gate matches it
against `allowed_commands` by prefix and never looks at the target file. So an entry admitting
`python3 externalized-memory/atomic-write.py` lets that script write anywhere.

**Design (recommended):**
1. plan-gate recognizes a segment whose program is `python`/`python3`/`py` running a script whose
   basename is on a writer list, and treats the script's target argument as a file write. That
   write is gated by allowed_paths, log self-protection, worktree claim and §3 leases, exactly
   like a `>` redirect, which segment checking already handles.
2. Built-in writer list: `atomic-write.py` (target = its only positional argument). Extendable with
   `PLAN_GATE_WRITER_SCRIPTS=name.py:argpos,...`.
3. A recognized writer whose target can't be determined (missing argument, variable expansion)
   → deny. Same rule as unsafe expansion today.

**Calls:**
- **Q7 — configurable list.** **Recommended:** built-in `atomic-write.py` plus the env extension.
  Alternative: built-in only.

## §5 Bypass values (#14) — mechanical

Every module's bypass and on-switch accepts `on`, `1`, `true` and `yes`, case-insensitive. Today
`MEMORY_CAP_BYPASS=on` silently does nothing. The docs list the accepted values once, in the
README. Nothing gets looser: a value that used to mean "off" still does.

## §6 ESLint legacy config (#4) — mechanical

Add a CommonJS entry (`index.cjs`) behind conditional exports (`"require"`) that exposes the same
`configs`, including `recommended-legacy`. Test it on ESLint 8.57 with Node 18 and 20, and the
flat config on ESLint 9. Bump the version to `0.1.2`. **Publishing to npm is the maintainer's
step.**

**Coordination (2026-10-16):** a parallel branch is adding two ESLint rules
(`no-tautological-assertions`, `no-unasserted-result`). It edits `index.mjs`, `package.json` `"files"`
and README. This track owns `index.cjs`, `package.json` `"exports"` and the version. Whichever PR
merges second must register every rule in both entries.

---

## Build order, verify, cost

- **Docs PR (now, independent):** the 10 doc-only items, the README rewrite for maintainer wording
  review, and interim honest wording for §1–§4 targets. About 3 agents.
- **Code PR (after this spec is approved):** §5 → §4 → §2 → §3 → §1, each built → adversarially
  verified → fixed, then the strong wording restored where a test backs it. §1 and §3 change the
  flagship's decision path, so each gets 2 verify lenses. Plan-gate tests must still pass on all
  three CI operating systems. About 8 agents.
- **ESLint PR:** about 2 agents.
- Merges are the maintainer's, as is `npm publish`.

**Calls summary:** Q1 default for approval · Q2 hash scope · Q3 TTY check · Q4 MCP matcher ·
Q5 lease-held behavior · Q6 strict-lease default · Q7 writer list.

## Decision log

**2026-10-16 (maintainer):** every call took the recommended option.
- Q1: approval is required whenever `PLAN_GATE_LOG_DIR` is set, and the example sets it.
  `PLAN_GATE_REQUIRE_APPROVAL=off` is the explicit opt-out.
- Q2: the approval hash covers the whole plan file.
- Q3: `approve` requires a TTY. `--no-tty-check` exists and is logged on the row.
- Q4: the example matcher adds `mcp__.*`.
- Q5: an edit to a path leased by another agent is denied.
- Q6: strict lease mode (`PLAN_GATE_REQUIRE_LEASE=on`) is opt-in.
- Q7: `atomic-write.py` is built in, extendable with `PLAN_GATE_WRITER_SCRIPTS`.
