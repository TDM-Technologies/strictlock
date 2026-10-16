# Claims-fix spec — make the stale claims TRUE (reword only where fixing isn't worth it)

Date: 2026-07-06. Status (2026-10-16): **PR 1 (rewords) in review 2026-10-16; §1 plan-gate part
superseded by [AUTHORIZATIONS-RECORD-SPEC.md](AUTHORIZATIONS-RECORD-SPEC.md) / PR #11 (shipped
2026-07-15); memory-cap mirror + scope-lease/sink-resolver log gap-fills still open.** §5 was fully
decided 2026-07-07 (Tim, one-by-one walkthrough). PR 1 = reword pass (ledger in §7); PR 2 = code
fixes. See §5 for each ruling + rationale, §6 for build order. Line numbers below are from July;
PRs #10–#12 moved text, so claims are re-located by wording, not line number.

The 2026-07-05 truth-pass (PR #10) corrected docs *to match* reality. This spec inverts the
preference: for each remaining stale/overclaimed public claim, **first** evaluate changing the
code/CI so the claim becomes true; reword only where the fix isn't worth building.

Evidence base: a 13-surface claims audit (every public doc checked against actual code/tests/CI,
each flagged claim independently adversarially re-verified). **56 confirmed wrong/overclaimed,
6 flags overturned** (notably: the 228-tests/10-modules figure, npm v0.1.1, and README thesis
move 4 all check out). Run cost for the record: 75 agents, ~3.84M tokens, ~17 min.

Alignment with the ❄ pause (STATE.md): the repo is link-only portfolio evidence — this work makes
the evidence read true. Docs-only rewords are zero-risk; code fixes below are small/medium and
gate-strengthening, not a new harvest wave. Sequencing is Tim's call (§5 Q1).

---

## 1. Fix A — opt-in allow+deny decision log (plan-gate) — makes the compliance story true

**Claims it makes true:** README.md:19 ("per-decision audit trail"), paper.md:271-272 ("every
gate decision (allow and deny) is recorded"), paper.md:290 (§6 CC7 row: "every gate decision,
*allow and deny*" … "tamper-evident").

**Reality today:** the general path logs denies only (`_log_deny` → `plan-gate-denies.log`,
opt-in via `PLAN_GATE_LOG_DIR`, best-effort). Allow-AND-deny per-call logging already exists
in-house for two side channels (`_log_ps_call`, `_log_mcp_file_write_call`) — the pattern is
proven; the flagship Edit/Write/Bash path just never got it. COMPLIANCE.md's "Honest scope"
block even records the intent ("the design intent is the full allow/deny record").

**Design:**
1. Generalize `_log_deny` → `_log_decision(decision, check, tool, tool_input, reason, plan)`;
   add a `gated_allow` closure mirroring `gated_deny` (plan-gate.py:828-831) and route every
   `allow()` through it — including the pre-plan-resolution bypass allows (read-only :854-863,
   always-writable :847-848, and the `PLAN_GATE_BYPASS` emergency allow :816 — an auditor wants
   exactly those recorded, tagged with their bypass class; empty `plan` field is honest there).
2. Sink: `plan-gate-decisions.log` (JSONL), opt-in via **`PLAN_GATE_DECISION_LOG=1`**, default
   off (allows are high-volume). `plan-gate-denies.log` behavior byte-for-byte unchanged.
3. Row = existing `_log_deny` shape + `decision: allow|deny` + `check: <rule that fired>`.
   Same discipline as every existing logger: best-effort, never blocks the gate, no payloads.
4. **Tamper-evidence (§5 Q2):** (a) hash-chain the decisions log (`prev` = SHA-256 of prior
   line; genesis "") + a ~40-line `plan-gate/verify-log.py` chain-walker → "tamper-evident"
   becomes literally true (edits/gaps detectable — best-effort log failures show as detectable
   gaps, which is correct tamper-*evidence*); or (b) one-word reword → "append-only".
5. Docs once built: CONFIG.md +1 env var; paper §5/§6 gain "when a log dir is configured" (the
   deny log was always opt-in the same way); COMPLIANCE.md CC7 row + Honest-scope block updated
   (it goes stale in the *opposite* direction once this ships); no test today asserts any allow
   row — add them (allow-row on allowed call; deny lands in both logs when flag on; flag off →
   no decisions log, denies unchanged; log failure never blocks; chain-verify + tamper-detect
   if (a)).

**Audit-trail completeness siblings (same claim family, small):**
- scope-lease/CONFIG.md:87 "every acquire and release … the owner" — release row omits owner
  (it's already resolved in main): 3-line fix + test. Optionally reclaim detail per row.
- sink-resolver/CONFIG.md:89 "every resolve / escalate / error" — the `check` verb and the
  repo-root failure path log nothing: add `_log_decision` calls + 1 test.
- memory-cap's deferred JSONL decision log (BACKLOG:126-127, explicitly tied to CC7): becomes a
  mechanical mirror (`MEMORY_CAP_DECISION_LOG=1`) — scope call in §5 Q3.

## 2. Fix B — compound-command segmentation (plan-gate) — the sweep's biggest find

**Claims it makes true:** README.md:29 ("run only the exact commands … everything else is
denied"), paper.md:143 ("every command — is intercepted"), paper.md:239 (Momentum Bias:
"**Prevents.**"), AUTHORING.md:133 ("a compound command is only as authorized as its
least-authorized segment"), plan-gate/README.md:20.

**Reality today (verified, security-relevant):** `allowed_commands` is prefix-matched
(`cmd.startswith(ac)`, plan-gate.py:1009-1011) and segment-checking fires **only for cd-led
compounds** (:1004-1008). An entry `pytest` admits `pytest -q && rm -rf /`. The read-only
bypass (:115-148, :854-863) is likewise prefix-only with no compound check (`cat x; rm -rf /`
rides through) and includes generous prefixes (`find ` admits `find -delete`; `echo `, `git
fetch`). Top-level redirections aren't treated as writes (`echo x > file` bypasses
allowed_paths). AUTHORING.md:133's "least-authorized segment" sentence is true for cd-compounds
only — currently false in general.

**Design:**
1. Segment-check **every** Bash command: run through the existing quote/subshell/heredoc-aware
   `_split_top_level` (add `|` to the split set), require each segment to independently pass
   (read-only prefix, git -C shape, cd no-op, or allowed_commands prefix) — exactly what
   `_check_cd_compound` already does for its case, generalized.
2. Treat top-level `>` / `>>` redirection targets as writes gated by `allowed_paths`.
3. Tighten the read-only list: drop/constrain `git fetch`, `git remote` (→ `-v`/`show`),
   `find ` (redirection/`-delete` risk), `echo `.
4. Micro-reword stays honest even after the fix: "exact commands" → "enumerated command
   prefixes, checked segment-by-segment" (matching per-segment prefix semantics, which
   SCHEMA.md/AUTHORING.md already document as deliberate). Paper §5 "Prevents." becomes
   defensible as written.
5. **Behavior change** — plans that relied on prefix-riding compounds start denying (that is
   the point). Existing tests pin prefix behavior (test_plan_gate.py:138-144 stays green —
   flags on one segment are fine); add deny tests for chained/redirected smuggling.

Effort: medium. This is the thesis of the product — the fix closes a real gate weakness AND
makes the flagship claims true. (§5 Q4 for the go/no-go.)

## 3. Fix C — CI OS matrix + genuine bugs the sweep found (fix regardless of claims)

**C1 — OS matrix** (BACKLOG `ci-os-matrix`, ready): CI is ubuntu-only across all 4 jobs while
CONFIG.md documents Windows/macOS usage. Python job → `os: [ubuntu, windows, macos]`, full
py-version matrix on ubuntu, endpoints (3.8 + 3.12) on the other two (9 python jobs). Shell
job: + macos for the bash tests; shellcheck/node/secret-scan stay ubuntu. Expect real failures
(path-sep, `python` vs `python3`, symlinks in liveness `_canon`, CRLF) — each is a genuine bug
to fix in-branch. Also fixes the "9 CI jobs" figure going stale — dev docs say 9, actual is 8
(reword regardless, see §4).

**C2 — real bugs (small, high-confidence, each + regression test):**
- **liveness-scan attribution:** `worktree_bypass: true` plans are documented as excluded from
  attribution (README:59, SCHEMA:57) but a bypass plan carrying an absolute path under the
  worktree still attributes — one-line fix in `plan_specifically_claims` (liveness-scan.py:243).
- **test-protection-guard pre-commit door:** env-strip drops `GIT_INDEX_FILE`, so `git commit
  -a`/`git commit <paths>` commits aren't seen as staged in precommit mode (README:119's "sees
  every commit" is currently false) — preserve `GIT_INDEX_FILE` in precommit mode.
- **test-protection-guard `-C` retarget:** `git -C <dir> commit` is recognized as a commit but
  analyzed against the wrong repo — extract the `-C` target during tokenization (README:118).
- **scope-lease release boundary:** SCHEMA.md:30 "never a silent acquire-over" — release path
  skips the refstore assert; add `_assert_same_refstore` in `release_lease` → exit 6 + test.
- **sink-resolver coverage-guard restore:** SCHEMA.md:112 "leaves the merge exactly as found" —
  escalation restores only *conflicted* sinks; snapshot/restore ALL configured sinks (:449,:462).
- **memory-cap NotebookEdit baseline:** README:22 "never re-flagged … cannot self-wedge" — the
  NotebookEdit path baselines against "" (can wedge); baseline against on-disk content.
- **commit-msg-gate "body" grep:** the `plan:` line is grepped over the whole message including
  the subject; restrict to the body (README:18) — or one-word reword ("message").
- **prepush examples walkthrough:** documented exit codes only reproduce with the generator cwd
  pinned — add one `export` line to the Setup block (examples/README.md:22-25).
- **liveness `_env_int`:** `<= 0` falls back silently; doc says announced — add the stderr
  notice.

**C3 — small feature-fixes that make module claims true:**
- **commit-msg-gate:** README:30 "reference an *approved* plan" — slug is never validated. Add
  opt-in `COMMIT_MSG_GATE_PLANS_DIR` (slug must resolve to a plan file); plus a CI job running
  the gate over `origin/main..HEAD` messages so "--no-verify or uninstalled hook" is caught at
  the merge boundary ("applies to every commit" becomes true at that boundary).
- **memory-cap total cap:** README.md:31 + memory-cap/README.md:3 say "caps the **size** of
  the index" — shipped cap is per-line only. Add opt-in `MEMORY_CAP_MAX_ENTRIES` /
  `MEMORY_CAP_TOTAL_CHARS` in the same changed-region pass → headline literally true.
- **liveness-scan examples:** README:101 promises "a sample plan" — add
  `examples/plan.example.md`; `--explain` docstring promises per-worktree internals — emit
  them (or match CONFIG.md's "counts" wording).

## 4. Reword-only pass (fix not possible / not worth it / accuracy-of-history)

| Where | What | Note |
|---|---|---|
| README.md:21 | blockquote module list names 5 of 11 modules | make non-exhaustive or extend |
| README.md:41 | "gates configured entirely by env vars" | eslint sibling = rule options |
| README.md:45 | "(…a compliance-mapping doc…)" — shipped 06-25 | swap for governance/marquee |
| roadmap.md:3 | family enumeration omits test-protection-guard | add to Gates |
| COMPLIANCE.md:8 | intro covers 6 of 11 modules | name all or "all shipped modules" |
| COMPLIANCE.md:190 | "blocks *every* tool call" (worktree guard) | read-only inspection stays available — reword, or see §5 Q6 |
| generated-sink-commit-gate/README.md:13 | "structurally impossible" | git honors `--no-verify`; unfixable client-side (server-side note already present) |
| memory-cap/README.md:47 | "a misconfiguration can't silently disable the cap" | semantically-wrong-but-valid regex can; unfixable in code |
| test-protection-guard/README.md:43 | "self-correct *this turn*" via decision reason | harness-dependent channel; soften to transcript/log |
| dev/STATE.md:6 | git_ref e4e94fd stale | → 5c55369 (PR #10 on top) |
| dev/STATE.md:98 + BACKLOG.md:45 | "9 CI jobs" | actual 8 (until C1 changes it again) |
| dev/BACKLOG.md:57 | Wave 4 "planned" incl. test-protection-guard | 1 of 4 shipped (PR #7) |
| dev/BACKLOG.md:189 | COMPLIANCE discoverability "ready" | done (README:88, roadmap:10/48) — move to Done |
| dev/STATE.md:142 | "no machine paths" vs a staged-import machine path in STATE:25 + HARVEST-SOURCE-MAP:86 | genericize the staging refs; optionally add a machine-path pattern to the CI secret scan |

## 5. Decisions routed to Tim (each is a real design call, not mechanics)

- **Q1 — sequencing vs the ❄ pause:** ✅ **DECIDED (Tim, 2026-07-07): two PRs.** PR 1 = full
  reword pass (§4 + interim honest wording on every fix-target claim) — the repo reads true at
  that merge. PR 2 = code fixes, restoring the stronger wording where the code now backs it.
- **Q2 — tamper-evident:** ✅ **DECIDED (Tim, 2026-07-07): hash-chain + verifier (§1.4a),
  full version with cross-platform append lock** (`flock` on POSIX, `msvcrt.locking` on
  Windows — plan-gate supports both OSes, so single-writer scoping was rejected). PR 1
  interim-rewords "tamper-evident"→"append-only"; PR 2 restores it with the chain + a
  documented tamper-evident-not-tamper-proof caveat (off-box head-hash stays a deployment
  responsibility, matching COMPLIANCE.md honest-scope framing).
- **Q3 — Fix A scope:** ✅ **DECIDED (Tim, 2026-07-07): include the memory-cap mirror** —
  same row shape + hash chain via a shared helper, own `MEMORY_CAP_DECISION_LOG=1` flag,
  own tests. Closes BACKLOG:126-127; every gate in the suite then has a durable decision
  trail with uniform tamper-evidence.
- **Q4 — Fix B go/no-go:** ✅ **DECIDED (Tim, 2026-07-07): GO, full design** — segmentation
  on `&&`/`||`/`;`/`|`, redirection-as-write, read-only list tightening (final read-only
  list settled during build against tests — once redirections are gated, bare `echo` may
  stay). Migration note for adopters riding compounds. Deliberate behavior change to the
  flagship's matching semantics, accepted.
- **Q5 — plan-gate crash posture:** ✅ **DECIDED (Tim, 2026-07-07): deny on crash** — parse
  failure or unhandled exception → deny, with `PLAN_GATE_BYPASS` honored **in the crash path**
  (top-level handler checks it before deciding) + loud actionable error message. README:84
  principle stays as written and becomes true; suite becomes uniformly fail-closed (matches
  memory-cap). Accepted risk: harness protocol drift denies until bypassed/updated — the
  logged bypass is the escape hatch, and its use lands on the Fix-A audit trail. Reverses the
  docstring's deliberate fail-open tradeoff; update the docstring's defense accordingly.
- **Q6 — unknown-tool posture:** ✅ **DECIDED (Tim, 2026-07-07): reword now + backlog the
  designed inversion.** PR 1 rewords README:16 → "every file edit, shell command, and
  recognized external-write tool call is intercepted…". New BACKLOG item: default-deny
  unknown tool names with a designed escape hatch (plan-level `allowed_tools:` frontmatter —
  a schema change through SCHEMA.md/AUTHORING.md, deliberately not rushed into PR 2).
  Rationale recorded: unlike Q5, the deny fires when nothing is wrong (any MCP server's
  tools are unknown names), so the escape hatch needs design time.
- **Q7 — "single-use" bypasses:** ✅ **DECIDED (Tim, 2026-07-07): reword** (COMPLIANCE.md:258,
  memory-cap README:48 + deny-message wording) → "single-use by convention: set inline per
  command; every use is logged, so a lingering bypass is visible in the trail." Rationale
  recorded: not an attacker-rideable hole; post-Fix-A the chained audit trail makes lingering
  bypasses unmissable (visibility over enforcement); token-file one-shot rejected for
  concurrency state + worse inline UX. "Uniquely-named" and "logged" halves stay — they're true.
- **Q8 — bigger-than-small items:** ✅ **ALL DECIDED (Tim, 2026-07-07):**
  - **Q8a prepush tree-vs-SHA gap → reword + CI check job.** Hook docs get honest tree-vs-tip
    wording; new CI job regenerates + diffs the sink on the PR head — validates the pushed
    commit at the merge boundary for every arrival path (`--no-verify`, uninstalled hook,
    tree/tip divergence). In-hook temp-worktree ref validation: not built, likely never needed.
    Final wording: "the hook checks your tree (fast, local); CI checks the pushed commit
    (authoritative)."
  - **Q8b `CEILING_MIN` → implement true WP-age.** Time since earliest branch commit not on
    base; past the ceiling escalates even on a fresh signal, matching all three doc sites.
    Rebase-rewrites-the-proxy caveat documented. Lowest-risk code fix in the spec (read-only
    reporter — a wrong escalation is noise, not a block).
  - **Q8c worktree hard-guard → composite fix.** Diagnostic deny fired from the real no-claim
    path ("N active plans exist, none claims this worktree — likely drift"), dead guard block
    (plan-gate.py:969-978) removed, docs rewritten to claim-by-absolute as the actual
    mechanism. No change to what's denied. `worktree:` frontmatter anchor parked in the same
    future schema wave as Q6's `allowed_tools:`.

## 6. Build order (all §5 decisions in)

**PR 1 — rewords only (zero-risk; repo reads true at merge):** the §4 table + interim honest
wording on every PR-2 fix target ("tamper-evident"→"append-only", "exact commands"→prefix
wording, README:16 enumerated coverage, "single-use"→by-convention wording — Q7's is permanent,
the others get re-strengthened by PR 2), + BACKLOG/STATE refresh incl. the two new backlog
items (Q6 `allowed_tools:` inversion; future schema wave also holds Q8c's `worktree:` anchor).

**PR 2 — code (build → adversarial-verify → guard-land, per piece):**
1. Fix A: `_log_decision` + `gated_allow` + hash-chain + cross-platform append lock +
   `verify-log.py` + memory-cap mirror (Q2/Q3) + the scope-lease/sink-resolver log gap-fills.
2. Fix B full: segmentation on `&&`/`||`/`;`/`|`, redirection-as-write, read-only tightening
   settled against tests (Q4). Heaviest verify pass — flagship matching semantics.
3. Q5 crash posture: deny on parse-failure/exception, bypass honored in crash path.
4. Fix C: C1 OS matrix (iterate real failures to green) + C2 nine small bugs + C3 features +
   Q8a CI sink-check job + Q8b WP-age + Q8c composite.
5. Restore strengthened claims that PR 1 interim-reworded, now code-backed.
6. Close out: BACKLOG (`paper-§6-reconcile`, `ci-os-matrix`, memory-cap decision-log → done),
   STATE refresh, spec archived with dispositions.

*(Mechanical note: PR 2's pieces are independent; if review size warrants, steps 1–4 can land
as stacked PRs off the same branch without revisiting Q1 — same two-stage truth semantics.)*

*Full per-claim verify evidence (file:line both sides, every finding) lives in the session's
sweep output; this spec is the durable triage. Overturned-on-verify (docs fine, no action):
README:56 thesis move 4, README:35 v0.1.1, README:79 CONFIG OS docs, STATE:9 228/10 figure,
scope-lease.py:43 docstring, liveness CONFIG:60 Windows example.*

---

## 7. PR 1 ledger (2026-10-16)

What PR 1 changed, claim by claim, so Stage 2 (PR 2) can restore the strong wording exactly where
code now backs it. Claims were re-located by wording, not by the July line numbers. Verified against
`plan-gate.py` at `origin/main` 81cad04 on 2026-10-16: `allowed_commands` and the read-only bypass are
prefix-matched; only `cd`-led compounds are split and checked per segment; a top-level `>` is not treated
as a write; a stdin parse failure or an unhandled exception exits 1 (non-blocking, so the action
proceeds — fail OPEN); the worktree "silent drift" deny block is unreachable because
`find_active_plans` already filters plans by absolute-path claim.

**Kinds.** *permanent* = final wording, no restore. *INTERIM-restore-in-PR2* = minimal honest wording
that PR 2 reverts or strengthens once the named fix lands. *already-true-skipped* = checked, claim holds
today, left alone. *gone* = the claim site no longer exists.

| File | Old wording (short) | New wording (short) | Kind | Fix that restores it |
|---|---|---|---|---|
| `README.md` (intro, thesis move 1) · `plan-gate/paper.md` §3.1, §7.1 · `externalized-memory/DESIGN.md` | "every consequential tool call is intercepted" | "every file edit, shell command, and recognized external-write tool call" | permanent | none (Q6); the `allowed_tools:` inversion is a BACKLOG item |
| `README.md` blockquote | names 5 of 11 modules | "…and the others in the table below" | permanent | none |
| `README.md` | gates "configured entirely by environment variables" | Python/shell gates by env vars; ESLint plugin takes rule options | permanent | none |
| `README.md` | roadmap pointer lists "a compliance-mapping doc" | "the governance-of-governance work" | permanent | none |
| `roadmap.md` | Gates family omits test-protection-guard | added | permanent | none |
| `COMPLIANCE.md` intro | covers 5 modules | names all modules, rows A–M, eslint plugin noted in row M | permanent | none |
| `COMPLIANCE.md` row F · `plan-gate/CONFIG.md` · `plan-gate/AUTHORING.md` §3 · `plan-gate/paper.md` Stage 4 + §5 silent-drift row · `plan-gate.py` docstring | worktree "hard guard blocks every tool call" | a plan counts as active for a worktree only via an absolute `allowed_paths` claim (or `worktree_bypass`); otherwise gated writes and non-read-only commands are denied, read-only inspection stays | permanent (accuracy) | Q8c composite fix will add the diagnostic deny and may reword these sites; no strong form to restore. AUTHORING §3 "takeaway" now says name one absolute path (it previously said relative paths satisfy the guard, which contradicted §4) |
| `generated-sink-commit-gate/README.md` · `generated-sink-commit-gate.py` docstring | "structurally impossible" to commit a stale sink | "fail closed at commit time"; git honors `--no-verify` | permanent | none (server-side note already present) |
| `generated-sink-commit-gate/README.md` · `commit-msg-gate/README.md` + `.sh` header · `generated-sink-prepush-gate/README.md` | "applies to every commit/push" | "runs on every commit/push that goes through git's hook machinery (`--no-verify` skips it)" | permanent | none |
| `memory-cap/README.md` `MEMORY_CAP_PATH_RE` · `memory-cap.py` comment · `COMPLIANCE.md` row H | "a misconfiguration can't silently disable the cap" | an *invalid* regex falls back to the default; a valid-but-wrong pattern still matches the wrong file | permanent | none (unfixable in code) |
| `test-protection-guard/README.md` | "self-correct *this turn*" via decision reason | where the warning lands depends on the harness | permanent | none |
| `dev/STATE.md` · `dev/BACKLOG.md` (x2) | "9 CI jobs" | 8 (5 python + node + shell + secret-scan) | permanent (until the OS matrix changes the count) | `ci-os-matrix` (C1) changes the number again |
| `dev/STATE.md` Meta/Next Action/Active Threads · `dev/BACKLOG.md` Wave 4, discoverability, paper-§6-reconcile | stale `git_ref`, ❄ pause as current, Wave 4 "planned", discoverability "ready", reconcile "ready" | refreshed; pause marked ended (thawed 2026-09-29, history kept); Wave 4 "1 of 4 shipped"; the other three moved to Done | permanent | none |
| `dev/BACKLOG.md` | (absent) | two new entries: default-deny unknown tools with `allowed_tools:`; future schema wave with `worktree:` anchor | permanent | none (design work) |
| `dev/STATE.md` · `dev/HARVEST-SOURCE-MAP.md` · `dev/WORK-QUEUE.md` | staged-import machine paths | "the maintainer's local staging import" | permanent | none |
| `.github/workflows/ci.yml` secret-scan | secrets + UUID patterns only | adds a machine-path pattern (`/Users/<name>`, `<Drive>:\Users`) | permanent | none |
| `COMPLIANCE.md` row I · `memory-cap/README.md` + `memory-cap.py` (docstring, stderr line, allow reason, deny message) · `generated-sink-commit-gate/README.md` · `generated-sink-prepush-gate/README.md` · `commit-msg-gate/README.md` | "single-use" / "for a single call/commit/push" bypass | "single-use by convention: set inline per command; every use is logged, so a lingering bypass is visible in the trail" (memory-cap: logged on stderr; commit-msg-gate: announced on stderr, no durable record) | permanent (Q7) | none; "uniquely-named" and "logged" halves kept |
| `generated-sink-prepush-gate/README.md` (what it does, prevents, can't address) · `README.md` module row · `COMPLIANCE.md` row I | validates "the terminal state" and CI peer implied | "the hook checks your tree (fast, local); CI checks the pushed commit (authoritative)" with the CI half stated as planned / not yet built; "prevents" scoped to the working tree | INTERIM-restore-in-PR2 (CI half only; the tree-vs-commit wording is permanent) | Q8a CI sink-check job: change "planned" to present tense, point at the job |
| `generated-sink-prepush-gate/examples/README.md` | exit codes only reproduce with generator cwd pinned | one `export …GENERATOR_CWD="$PWD"` line added to Setup (reproduced before/after) | permanent (spec §3 C2 item, docs-only fix pulled into PR 1) | none |
| `README.md` · `plan-gate/README.md` · `plan-gate/SCHEMA.md` · `plan-gate/AUTHORING.md` §2/checklist · `plan-gate/paper.md` (Stage 4, §5 Momentum Bias "Prevents.", §6 row 1, §7.3) · `COMPLIANCE.md` row A · `plan-gate.py` docstring | "exact commands" / "every command checked" / "a compound command is only as authorized as its least-authorized segment" / Momentum Bias plain "Prevents." | "enumerated command prefixes"; per-segment checking stated as `cd`-led only today; AUTHORING gains a "Current limit" paragraph; Momentum Bias "Prevents" carries a caveat | INTERIM-restore-in-PR2 | Fix B: restore "enumerated command prefixes, checked segment by segment", the general least-authorized-segment sentence, and plain "Prevents."; delete the "Current limit" paragraph and the caveats; mention redirection-as-write |
| `README.md` Design principles · `COMPLIANCE.md` row F · `plan-gate.py` docstring | "A bug in the gate fails toward safety" | caveat: gate crash / unparseable payload exits non-zero (non-blocking) so the action proceeds; deny-on-crash planned | INTERIM-restore-in-PR2 | Q5 deny-on-crash (bypass honored in the crash path): restore the principle line, delete the caveats, update the hook-contract docstring and the `plan-gate/README.md` "fails open for its own bugs" sentence |
| `plan-gate/README.md` "fails open for its own bugs" | (already honest) | unchanged | already-true-skipped | rewritten by the Q5 PR |
| `README.md` per-decision audit trail · `plan-gate/paper.md` §5/§6 "allow and deny", hash-chained · `COMPLIANCE.md` row B + honest-scope block · `plan-gate/CONFIG.md` decision log | the July "denies only / reword to append-only" sites | already rewritten by PR #11 and code-backed (tamper-evident chain, allow + deny, `verify-log`); not softened | gone (as stale-claim sites) / already-true-skipped | none; §1 of this spec is superseded for plan-gate |
| `test-protection-guard/README.md` pre-commit door table + known limits | "sees every commit" | "commits that run the hook"; `git commit -a` / `<paths>` may be missed (GIT_INDEX_FILE is stripped in `_clean_git_env`) | INTERIM-restore-in-PR2 | C2 "pre-commit door": preserve `GIT_INDEX_FILE` in precommit mode; restore "sees every commit" |
| `test-protection-guard/README.md` known limits | `git -C <dir> commit` recognised (implied correct) | recognised but analysis is not retargeted to `<dir>` | INTERIM-restore-in-PR2 | C2 "-C retarget": extract the `-C` target; drop the caveat |
| `scope-lease/SCHEMA.md` · `scope-lease/README.md` | "never a silent acquire-over" / foreign-store "hard stop (exit 6)" | scoped to `acquire` and `fence-check`; `release` runs no refstore check | INTERIM-restore-in-PR2 | C2 "release boundary": add `_assert_same_refstore` in `release_lease` (exit 6 + test); drop the qualifiers |
| `scope-lease/CONFIG.md` (env table + Decision log) · `COMPLIANCE.md` row J | "every acquire and release … the owner"; reclaim "logged with its incremented token" | successful acquire rows carry owner; release rows (only when something is released) have no owner; denied acquires write no row; reclaim token is on the stderr line | INTERIM-restore-in-PR2 | Fix A siblings: release row gains owner (3-line fix + test); decide whether a denied acquire should log; then restore "every acquire and release … owner" |
| `sink-resolver/CONFIG.md` (env table + Decision log) · `COMPLIANCE.md` row K | "every resolve / escalate / error" (incl. refusals) | only `resolve` outcomes log; `check`, repo-root failure, no-conflict and dry-run write no row | INTERIM-restore-in-PR2 | Fix A siblings: add `_log_decision` calls to `check` and the repo-root failure path (+1 test); restore the wording |
| `sink-resolver/SCHEMA.md` | coverage-guard escalation "leaves the merge exactly as found" | restores the *conflicted* sinks only; other rewritten configured sinks are not restored | INTERIM-restore-in-PR2 | C2 "coverage-guard restore": snapshot/restore all configured sinks; restore "exactly as found" |
| `memory-cap/README.md` · `memory-cap.py` docstring | "never re-flagged … cannot self-wedge" | true for `Write`/`Edit`; `NotebookEdit` has an empty baseline and is checked whole | INTERIM-restore-in-PR2 | C2 "NotebookEdit baseline": baseline against on-disk content; restore the sentence |
| `README.md` module row · `memory-cap/README.md` tagline + intro + new bullet · `memory-cap.py` docstring · `COMPLIANCE.md` row H heading/body | "caps the size of the index" | "caps the length of each entry"; per-entry only, no total limit yet | INTERIM-restore-in-PR2 | C3 total cap (`MEMORY_CAP_MAX_ENTRIES` / `MEMORY_CAP_TOTAL_CHARS`): restore "caps the size of the index" |
| `commit-msg-gate/README.md` · `commit-msg-gate.sh` header · `README.md` module row · `COMPLIANCE.md` row C | "reference an *approved* plan"; "body contains a `plan:` line" | "name a plan (`plan: <slug>`)", slug not validated, line grepped over the whole message; local hook, `--no-verify` skips it | INTERIM-restore-in-PR2 | C3 `COMMIT_MSG_GATE_PLANS_DIR` (slug must resolve to a plan file) + the CI job over `origin/main..HEAD`, then restore "approved plan"; C2 body grep restricted to the body, then restore "body" (or keep "message") |
| `liveness-scan/README.md` Files table | "a heartbeat-touch snippet and a sample plan" | "a heartbeat-touch snippet" (no sample plan exists in `examples/`) | INTERIM-restore-in-PR2 | C3: add `examples/plan.example.md`, restore the phrase |
| `liveness-scan.py` docstring `--explain` | "thresholds + per-worktree internals" | "thresholds + counts" (matches `CONFIG.md` and the code) | permanent (or restore if C3 chooses to emit per-worktree detail) | C3 option: emit per-worktree internals instead |
| `liveness-scan/CONFIG.md` threshold sentence | "non-integer or `<= 0` … announced on stderr" | non-integer is announced; `<= 0` falls back silently | INTERIM-restore-in-PR2 | C2 `_env_int`: add the stderr notice for `<= 0`; restore the sentence |
| `liveness-scan/README.md` · `SCHEMA.md` · `liveness-scan.py` (`plan_specifically_claims` docstring) · `dev/BACKLOG.md` | `worktree_bypass` plans "excluded" from attribution | "meant to be excluded"; a bypass plan with an absolute path under the worktree still attributes today | INTERIM-restore-in-PR2 | C2 attribution: one-line fix in `plan_specifically_claims`; restore "excluded" |
| `liveness-scan/README.md` · `CONFIG.md` · `SCHEMA.md` · `liveness-scan.py` docstring | `CEILING_MIN` is a whole-unit-of-work ceiling that escalates "even on a fresh signal" | outer quiet-time bound on signal age; does not track time since the unit began | INTERIM-restore-in-PR2 | Q8b true WP-age (earliest branch commit not on base; rebase caveat documented): restore all three doc sites. The `classify()` reason string "whole-WP ceiling" was left as is; it becomes true then |
| `generated-sink-*-gate/README.md` `*_TIMEOUT` rows | "can't silently disable the gate" | unchanged | already-true-skipped | none (fallback is loud) |
| `COMPLIANCE.md` rows L, M · `test-protection-guard/CONFIG.md`/`SCHEMA.md` | summaries of the above | checked; no separate claim needed rewording beyond the rows listed | already-true-skipped | none |
| `dev/WORK-QUEUE.md` | (copied from `784fe01`) | added a 2026-10-16 status note pointing at PR 1 | permanent | none |
| `COMPLIANCE.md` row A, row F · `plan-gate/CONFIG.md` worktree-guard bullet · `plan-gate/AUTHORING.md` §2 "Current limit" + §3 · `plan-gate/paper.md` §4 worktree guard, §5 Momentum Bias + Silent-drift rows · `plan-gate.py` docstring | "non-read-only commands are denied" / "nothing broader" / "Prevents." | read-only set named as prefix-matched: a chained or `>`-redirected command starting with a read-only prefix (`cat x; rm y`, `echo x > f`) is allowed today, with or without an active plan (added after the PR 1 review reproduced it) | INTERIM-restore-in-PR2 | Fix B (per-segment check of read-only prefixes, redirection-as-write, read-only list tightening) |
| `COMPLIANCE.md` row I "What an auditor can trace" | "every committed/pushed generated artifact provably corresponds … cannot enter the history unobserved" | scoped to artifacts that passed through the hooks; `--no-verify` path named; CI check planned | INTERIM-restore-in-PR2 | Q8a CI sink-check job |
| `README.md` modules row, generated-sink-commit-gate | "Stale generated files never get committed." | "Refuses a commit whose staged generated file is stale (… `--no-verify` skips it)" | permanent | none (client-side hooks are skippable) |
| `commit-msg-gate/commit-msg-gate.sh` header + rejection text | "its body contains" / "referencing the approved plan … plan files live in your plans directory" | "its message contains" / "the slug is not checked against your plans directory" | INTERIM-restore-in-PR2 | C2 body-only grep + C3 `COMMIT_MSG_GATE_PLANS_DIR` |
| `generated-sink-prepush-gate.py` docstring | "against the terminal branch state" | "against the working tree (not the pushed commit; CI check planned)" | INTERIM-restore-in-PR2 (CI half) | Q8a |
| `test-protection-guard.py` docstring | "lands IN the agent's context so it can self-correct" | harness-dependent channel | permanent | none |

**Stage-2 reminder.** The spec's July line numbers no longer apply; use the "Old wording" column as the
search key. When restoring a claim, also delete the matching "(fix in progress)" / "currently" / "not yet"
phrases (search for those three strings in `plan-gate/`, `scope-lease/`, `sink-resolver/`,
`liveness-scan/`, `test-protection-guard/`, `memory-cap/`, `commit-msg-gate/`, `generated-sink-prepush-gate/`,
`COMPLIANCE.md`, and `README.md`).

---

## 8. PR 2 dispositions (2026-10-16)

Branch `feat/claims-truth-code` (local, not pushed): `docs/claims-truth-reword` + four piece branches merged
`--no-ff` (plan-gate, state-modules, hook-modules, ci; no conflicts), then the restore commit and the close-out
commit. Each landed fix was checked against code and a test (not against the piece reports). Full suite
green on macOS: 366 Python tests + 30 shell checks. **Windows and macOS CI legs are unproven until the PR's
first CI run.**

**Built** (code + test)
- Fix B: per-segment Bash checking (`&&` `||` `;` `|` `&` newline, `$(...)`, backticks, `<(...)`), redirection-as-write,
  read-only list audit, unparseable commands denied. Q5: deny-on-crash with bypass honored and logged. Q8c: no-claim
  drift diagnostic. Empty `allowed_commands` entries match nothing.
- memory-cap: hash-chained decision log + `verify-log`; `NotebookEdit` baselines on-disk content; opt-in
  `MEMORY_CAP_MAX_ENTRIES` / `MEMORY_CAP_TOTAL_CHARS`.
- scope-lease: `release` runs the refstore check (exit 6); release rows carry owner; reclaim detail on acquire rows.
- sink-resolver: `check` outcomes and repo-root failure log rows; coverage guard restores all configured sinks.
- test-protection-guard: `GIT_INDEX_FILE` preserved in precommit mode; `-C <dir>` retargets the analysis.
- commit-msg-gate: `plan:` matched in the body only; opt-in `COMMIT_MSG_GATE_PLANS_DIR`; example CI job.
- liveness-scan: `worktree_bypass` plans excluded from attribution; `<= 0` thresholds announced; `examples/plan.example.md`;
  per-worktree `--explain`; true WP-age ceiling (committer-time proxy; rebase caveat documented).
- generated-sink-prepush-gate: example CI job (`examples/ci-sink-check.yml`).
- CI: 9-entry Python OS matrix (ubuntu 3.8-3.12, windows 3.8 + 3.12, macOS 3.8 + 3.12); shell job on ubuntu + macOS.

**Already present:** Fix A for plan-gate (PR #11); plan-gate's chain/lock/verifier reused as the model for memory-cap;
scope-lease acquire/fence already ran the refstore check; liveness `_env_int` already announced non-integers.

**Not built** (interim wording kept where a claim depends on it; listed in BACKLOG "Open after Stage 2")
- PowerShell compound parsing and redirect handling (docs name the gap).
- scope-lease: a denied acquire still writes no row.
- sink-resolver: a no-conflict `resolve` and a dry-run that would auto-resolve still write no row.
- memory-cap total caps are not evaluated for `NotebookEdit`.
- Edit/Write always-writable check compares lexically (`..` can escape); wider bypass-hunt families not run;
  shellcheck not run locally; commit-msg-gate subject-as-first-paragraph left as is.

**Design calls recorded by the pieces** (re-arguable): pre-plan `cd x && <read-only>` is now allowed; a plan entry
spelling out a whole compound no longer authorizes it; new decision classes `unparseable_command` and `gate_error`;
liveness ceiling escalates on signal age OR WP age; commit-msg slug resolution does not inspect plan `status`
(so the restored wording says "resolves to a plan file", not "approved plan").

**Restored claims** (§7 INTERIM rows)
- plan-gate "enumerated command prefixes, checked segment by segment", redirection-as-write, plain "Prevents", and
  the deny-on-crash / "a bug in the gate fails toward safety" line: README Design principles, COMPLIANCE rows A and F
  (module docs, paper, docstring were restored by the piece). PowerShell caveat kept.
- memory-cap "caps the size of the index" (README module row, COMPLIANCE row H; per-entry default, totals opt-in);
  decision log noted in row H. NotebookEdit self-wedge sentence restored in the module (piece).
- commit-msg-gate: README row and COMPLIANCE row C name the body-only `plan:` line, the opt-in plans-dir check and
  the example CI job; "approved" not restored (status is not inspected).
- generated-sink-prepush-gate: "planned / not yet built" replaced with the example CI job, stated as a job adopters
  add (README module row, COMPLIANCE row I twice).
- scope-lease (COMPLIANCE row J): release rows carry owner, reclaim detail on the acquire row; "denied acquires are not
  logged" kept. Release refstore qualifier dropped in module docs (piece).
- sink-resolver (COMPLIANCE row K): `check` and repo-root rows logged; no-conflict/dry-run qualifier kept.
  "Exactly as found" restored in SCHEMA (piece).
- test-protection-guard, liveness-scan: module docs restored by the pieces (sees commits that run the hook,
  `-C` retarget, excluded bypass plans, WP-age ceiling, sample plan, per-worktree `--explain`); BACKLOG liveness line fixed.
- CI-job counts: 8 -> 13 recorded in STATE; README/COMPLIANCE carry no job count.
