# Worker broker design — swappable local harnesses as Claude Code subagents

Draft 0.2, 2026-11-11. Home: `spielmaster` (agent/harness experiments). Prototype will
live in `integrations/worker-broker/`. v0.1 lived in the Strata checkout's scratch area;
this repo is the right home — the broker is harness-neutral by design.

## The problem in one paragraph

Opus (Claude Code, subscription, 100% native — no router in front of it) orchestrates a
project. Heavy, bulk, or parallel subtasks run locally as unattended workers on Strata's
Qwen, under a **swappable** harness. The harness is a plugin detail, not an architecture
decision: DSH is the first test; Qwen Code, Codex CLI, and Goose follow.

## Core idea: the broker owns the session, the harness owns the agent

Everything the orchestrator needs — session identity, message queue, status, reports,
escalation, notifications — lives in a small **broker daemon**. A harness adapter is
thin: start a turn, end a turn, stream events, set the permission mode. Whatever a
harness cannot do (e.g. mid-turn injection), the broker emulates or reports as
unsupported via capability flags. Swapping harnesses = swapping adapters; the Claude
Code side never changes.

```
Claude Code sessions (1..n, each with an MCP shim + monitor)
        | stdio MCP + per-session cursor
        v
   broker daemon (one, shared; state on disk)
        |-- fleet registry, event log, message queues
        |-- per-worker dir: transcript.jsonl, REPORT.json, artifacts
        |-- adapters: dsh | qwen-serve | codex-exec | goose
             each worker: own git worktree + branch

engine status: stays separate — Strata's existing MCP (strata_status / strata_models)
```

## Shared broker, leases, and takeover

The MCP stdio server Claude Code spawns is a **thin shim** per session; the real broker
is one shared daemon (local socket). Worker state lives on disk, so:

- Multiple Claude Code sessions attach happily; each keeps its own event-log cursor, so
  every session can see the fleet (and notifications are per-session, never spammed).
- **Takeover:** attention on a worker is a lease with heartbeat, not a hard attachment.
  If the holding session dies (crash, closed terminal), the lease lapses and any new
  session can claim watcher role — including a *different harness* orchestrating, since
  "orchestrator" is just another socket client.
- A worker never dies because its orchestrator died; only an explicit `worker_stop` or a
  policy timeout does that.

## Broker tools (the whole orchestrator contract)

| Tool | Returns |
|---|---|
| `worker_start(task, repo, mode, harness?, leash?)` | `id`; creates worktree `worker/<id>` via `git worktree add`, spawns turn 1; **fire-and-forget** |
| `worker_send(id, message)` | `delivered: live` (mid-turn) or `queued` (next turn boundary) — never silently dropped |
| `worker_status(id?, tail_lines?)` | per worker: state, elapsed, turn number, tokens in/out (null if the harness doesn't report them), queue depth, last N transcript lines |
| `worker_events(since_cursor, cap)` | new fleet events since your cursor (starts, reports, escalations) — the fallback for clients without push |
| `worker_report(id)` | REPORT.json contents (capped per output rules; full file path always given) |
| `worker_output(id, file, offset?, limit?, grep?)` | slice a large artifact safely (see output rules) |
| `worker_stop(id)`, `worker_list()` | control and fleet view |

States: `queued -> running -> (needs_input <-> running)* -> reported -> running (follow-up turn) ...`
A reported worker is a **resumable session**, not a dead process: `worker_send` after a
report starts turn N+1 (adapter resume where the harness has one; otherwise a fresh run
seeded with the previous report + transcript summary — the broker makes this invisible).

## Async by default: notifications, not blocking waits

The preferred pattern is **start -> background work -> notification**, no tool call parked
open:

- The daemon appends lifecycle events (report ready, escalation, failure) to the fleet
  event log. A **Claude Code plugin monitor** (plugin component, documented in the Nov
  2026 plugin docs) tails the daemon's event log from the session's cursor and injects
  each event into the session as a one-liner notification:
  `worker 42 reported: refactor done, 14 files, tests green — full: .workers/42/REPORT.json`
- The injected summary is **capped** (~1.5 KB) so a chatty worker can never blow up the
  orchestrator's context; the pointer to the full report is always included.
- Clients without monitors (other harnesses, plain MCP use) fall back to
  `worker_events(since)` — a cheap cursor poll, trivially wrapped in a tiny loop by the
  caller. No blocking wait tool in v1; a bounded `worker_wait` may return later only if
  real usage shows polling is worse — decision by measurement, not vibes.

Escalations (`needs_input`) use the same notification path: Opus gets poked exactly when
it is needed, and answers with `worker_send`.

## Large outputs: cap and address

Every broker tool's return payload is capped by default (~4 KB). When content is larger,
the result is a handle, never a blob:

```
{ truncated: true, bytes: 512340, file: ".workers/42/transcript.jsonl",
  head: "...", tail: "...",
  next: "worker_output(id=42, file=transcript.jsonl, offset=0, limit=200)" }
```

- Artifacts always exist as files in the worker dir (`transcript.jsonl`, `REPORT.json`,
  diff stats). Files are the *primary* large-output interface: the orchestrator can also
  just read/grep them with its own tools — shared filesystem, no broker round-trip.
- `worker_output` exists for safe slicing when the orchestrator prefers not to touch the
  filesystem blindly: offset/limit windows or grep, each response itself capped.
- Same rule for adapter->broker ingestion: worker stdout is streamed to the transcript
  file, not buffered into tool results. No unbounded string ever crosses MCP.

## Unattended operation and escalation

- No human TTY exists in a worker. The permission leash is set **per task at start**
  (`leash`: DSH profile / sandbox mode), default = write inside the worktree only.
- Two escalation kinds, both surfaced to the orchestrator, never to a terminal:
  1. **Approval blocked** — harness would ask a human; broker records it fail-closed,
     sets `needs_input`, and puts the pending action in the status. (DSH already does
     fail-closed: verified by Strata's `integrations/harness/tool_flow_e2e.py` check A04.)
  2. **Worker question** — task instructions require the worker to end its turn with the
     question in REPORT.json instead of guessing.
- The orchestrator answers both with `worker_send`; the queued answer resumes the turn.

## Capability matrix

Tags: **[V]** verified first-hand on this machine (2026-11-11); **[D]** documented, not
yet run; **[U]** unconfirmed assumption — verify when writing that adapter.

| Capability | DSH | Qwen Code | Codex CLI | Goose |
|---|---|---|---|---|
| Headless one-shot turn | `dsh headless "task"` **[V]** (help: "answer one task, print the result, and exit") | headless `-p` **[D]** | `codex exec` **[D]** | `goose run` **[U]** (docs reorg; CLI page 404s) |
| Session / resume | durable sessions; `dsh tui --resume <session>` **[V]**; headless resume **[U]** | continue/resume + daemon sessions **[D]** | `exec resume <id>` **[U]** (syntax) | session store **[U]** |
| Live mid-turn injection | inside DSH yes **[V]** (a running DSH session steers children at step boundaries); from outside: via web-profile/ACP **[U]** | daemon REST + ACP bridge + permission mediation pages exist **[D]** | none **[D]** | none **[D]** |
| Unattended permission | profiles; fail-closed without answerer **[V]** (A04) | approval mode + sandbox **[D]** | `--sandbox workspace-write`, approval policy **[D]** | extension access controls **[D]** |
| Machine-readable output | headless prints result; session records **[V-partial]** | structured output + daemon API **[D]** | `--json` **[D]** | recipe `-p` JSON schema **[U]** |
| Native sub-tree under the worker | yes **[V]** | SubAgents **[D]** | no | recipes **[U]** |
| Human view of the conversation | DSH web GUI, e.g. `http://127.0.0.1:3081` **[V running]** | daemon + TUI **[D]** | TUI `resume` **[D]** | Desktop **[D]** |
| Wire to Strata | profile, in use today **[V]** | OpenAI-compatible **[D]** | `/v1/responses`, built for it (Strata `docs/DETAILS.md:872`) **[V]** | OpenAI-compatible provider **[D]** |
| License / activity | MIT, co-developed with Strata | Apache-2, pushed 2026-11-11 | Apache-2, pushed 2026-11-11 | Apache-2, pushed 2026-11-11 |

Worker-seat ranking implied by the table: DSH (only fully verified column) -> Qwen Code
(strongest documented session substrate: daemon REST + permission mediation) -> Codex
(thin but boring) -> Goose (CLI contract currently in flux).

## Worktrees and the return path

- Broker owns `git worktree add` + branch naming (`worker/<id>-<slug>`); workers never
  touch the main checkout.
- Artifacts per worker dir (outside the worktree, e.g. `.workers/<id>/` in the repo,
  gitignored): `transcript.jsonl` (uniform, broker-normalized — also the human's
  `tail -f` viewer), `REPORT.json`, last-lines buffer for `worker_status`.
- Merge and delete stay with the orchestrator: bad worker = deleted branch.

## Claude Code integration

- **MCP is the integration** (stdio shim per session -> shared daemon; no network port).
- A thin **plugin** wraps it: `.mcp.json` + a skill that teaches Opus the worker recipe
  (when to delegate, how to read reports, how to answer escalations) + the **monitor**
  that delivers notifications + a `PostToolUse` hook printing a one-line fleet summary
  (`2 running, 1 needs_input — worker/42-fix-auth`). Plugin "mods/panes" might give a
  persistent status pane — **[U]**, try later.
- **Human view**: DSH workers get the real DSH web GUI conversation view; others get the
  uniform `transcript.jsonl`. Read-only from the human's side; replies go through the
  orchestrator.

## Capacity

The daemon keeps a semaphore sized to the Strata server: read `--parallel N` / context
config via `strata_status`; refuse `worker_start` past N with a clear "engine full"
error. Engine health stays a separate mechanism (Strata's MCP already answers it).

## Phases

0. **Quality gate, no code.** Five task classes (bulk grep+summarize, test triage,
   mechanical rename, doc writing, hard reasoning) run by hand through `dsh headless`
   against Strata. Only DSH is installed on the test machine today, so this runs first.
1. **Broker MVP, DSH adapter only**: daemon + shim, start/status/events/report/
   send(queued)/stop, worktrees, transcript mirror, notifications via the monitor, one
   concurrent worker. Exit criterion: Opus runs a 3-worker refactor batch unattended
   end-to-end, backgrounded, with takeover after a simulated orchestrator death.
2. **Swap proof**: Qwen Code adapter (daemon mode; tests live injection) and Codex
   adapter (tests the lowest-common-denominator path). A harness swap must not touch the
   MCP surface, the plugin, or any task text.
3. Goose adapter only if its CLI contract settles; plugin packaging + status polish.

## Decisions log

- **Repo/prototype home**: this repo; prototype under `integrations/worker-broker/`.
  (2026-11-11, user)
- **Turn-boundary delivery is acceptable for v1**; live injection is phase 2.
  (2026-11-11, user)
- **Async by default**: notification (monitor) rather than blocking wait; capped
  summaries with file pointers. (2026-11-11, user)
- **Large outputs**: cap-and-address; files are the primary interface; slicing tool.
  (2026-11-11, user)
- **Shared broker daemon** with leases and takeover; multiple Claude Code sessions and
  other harness orchestrators play nice. (2026-11-11, user)
- **Tokens**: report when a harness exposes usage, null otherwise — no invented numbers.
  (2026-11-11, user)

## Still open

1. **DSH external session channel** for phase 1: `dsh headless --resume <session>`
   (does it exist? one-line check in the user's terminal: `dsh headless --help`) vs
   driving the web-profile session API vs the ACP profile. Decides adapter size for the
   resume turn; everything else works either way.
2. **Monitor reliability**: confirm the Claude Code plugin monitor actually injects into
   a live session (docs list it as a component; behavior needs a phase-1 spike). Fallback
   is already designed: cursor polling.
