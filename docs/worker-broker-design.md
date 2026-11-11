# Worker broker design — swappable local harnesses as Claude Code subagents

Draft 0.6, 2026-11-11. Home: `spielmaster` (agent/harness experiments). Prototype will
live in `integrations/worker-broker/`. v0.1 lived in the Strata checkout's scratch area;
this repo is the right home — the broker is harness-neutral by design.
0.3: verified the `dsh headless` flag contract (resume, leash, `--json` events); DSH
adapter sketch added. 0.4: phase 1.5 added — DSH as second orchestrator via MCP, with
DSH web GUI as the worker conversation viewer; Strata serving verified live.
0.5: phase 0 running — fixtures + runner under `integrations/worker-broker/phase0/`;
headless worker reached Strata (smoke passed); `--json` line shape and session-id
discovery measured; worker model-config recipe documented.
0.6: phase 0 complete — all 6 runs exit 0, all tasks A/S-graded (scorecard below);
`--approval-policy never` = auto-approve (probe wrote to /tmp unchallenged); MCP
declaration resolved from upstream docs and **live-verified** end-to-end
(`mcp__spike__pong` round-trip through a headless run); `tool_call` field shape
corrected to `{callId, tool, input}`.

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
| Session / resume | headless resume: `--session-id <id>` **[V]**; id arrives in the *first* `--json` event `{"type":"session","sessionId":…,"cwd":…}` **[V smoke]** | continue/resume + daemon sessions **[D]** | `exec resume <id>` **[U]** (syntax) | session store **[U]** |
| Live mid-turn injection | inside DSH yes **[V]** (a running DSH session steers children at step boundaries); from outside: via web-profile/ACP **[U]** | daemon REST + ACP bridge + permission mediation pages exist **[D]** | none **[D]** | none **[D]** |
| Unattended permission | per-run `--permission-mode <preset>` + `--approval-policy ask\|never`, `--add-dir <path>` (their example: sharing `/repo/.git`) **[V flags]**; fail-closed without answerer **[V]** (A04) | approval mode + sandbox **[D]** | `--sandbox workspace-write`, approval policy **[D]** | extension access controls **[D]** |
| Machine-readable output | `--json`: newline-delimited events — vocabulary observed: `session`, `status`, `thinking`, `tool_call`, `tool_result`, `text`, `final`; `tool_call` carries `{callId, tool, input}` (not name/args), MCP tools appear as tool `mcp__<server>__<tool>` **[V]** | structured output + daemon API **[D]** | `--json` **[D]** | recipe `-p` JSON schema **[U]** |
| Native sub-tree under the worker | yes **[V]** | SubAgents **[D]** | no | recipes **[U]** |
| Human view of the conversation | DSH web GUI `http://127.0.0.1:3081` **[V running]**; session store shared across profiles, keyed by workspace path (`~/.dsh/sessions/<workspace-slug>/session-<uuid>`) **[V layout]** — whether the GUI renders a *headless* session **[U spike 1.5]** | daemon + TUI **[D]** | TUI `resume` **[D]** | Desktop **[D]** |
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

## Adapter sketch: DSH (contract verified from `dsh --profile headless --help`, 2026-11-11)

The DSH adapter is almost embarrassingly thin — the broker's concepts map 1:1 onto flags:

| Broker concept | DSH headless |
|---|---|
| turn 1 | `dsh --profile headless --json --permission-mode <preset> --approval-policy never --add-dir <worktree>/.git - ` (task on stdin; answer → stdout, diagnostics → stderr) |
| follow-up turn (resume) | `dsh --profile headless --json --session-id <id> …` — "adopt the persisted Session with this id" |
| leash (`worker_start`) | `--permission-mode` preset + `--approval-policy never`; `--add-dir` for whitelisted extras (their own example shares `/repo/.git`) |
| transcript feed | `--json` newline-delimited run events on stdout → mirrored to `transcript.jsonl` |
| worker identity | the DSH session id — arrives in the first `--json` event: `{"type":"session","sessionId":"session-<uuid>","cwd":…}` **[V smoke]**; `final` event carries `reason.kind` + answer text **[V smoke]** |

The worker's session persists under `~/.dsh/sessions/<slug-of-cwd>/` — the slug is the
worker's cwd, so the broker *knows* where a worker's transcript lives the moment the run
starts **[V smoke]**. That is also the hook for phase 1.5: a GUI session opened at the
worker's cwd sees the same store.

### Worker model config (how headless reaches Strata) **[V smoke]**

Headless ships with an empty patch layer, so it has no providers until you give it one.
The recipe, copied from the web/desktop profile pattern
(`~/.dsh/profiles/web/cordis.patch.yml`): patch `llm-pi-ai` in
`~/.dsh/profiles/headless/cordis.patch.yml` with provider `local` (api
`openai-completions`, `baseURL: http://127.0.0.1:8080/v1`, model + context window as
served), plus `agent-default-model` pointing at it. The key travels in
`apiKeyEnv: LOCAL_API_KEY`; the broker should inject it per spawn from
`strata launch --show-key` (verified: Strata returns 401 without the key, 200 with it).
One consequence for the adapter: each harness run is keyed by its *cwd*, so one worktree
per worker gives clean session separation for free.

Mid-turn `worker_send` has no DSH headless flag — turn-boundary delivery only, which is
the accepted v1 behavior.

## Phases

0. **Quality gate, no code.** Five task classes (bulk grep+summarize, test triage,
   mechanical rename, doc writing, hard reasoning) run by hand through
   `dsh --profile headless` against Strata; plus two free measurements: where the
   session id appears in `--json` events **[V — resolved, first event]**, and
   `--approval-policy never` behavior on a would-be approval **[V — resolved]**:
   auto-approve, not fail-closed (probe wrote `/tmp` unchallenged). Only DSH is
   installed on the test machine today, so this runs first. Preconditions verified
   2026-11-11: Strata serving `qwen3.8-flash-next-iq3_s`
   at 127.0.0.1:8080, 128k context, API key on (`/health` ok,
   `strata launch --status`: `iq3_s-128k ready`). Workers will share the server that
   serves the orchestrator, so phase 0 runs one task at a time.
   Fixtures + runner live at `integrations/worker-broker/phase0/`: a small seeded Python
   package (`fixtures/acme/`) with exactly one real bug (leap-year February proration),
   one order-dependent flaky test, seven `audit()` call sites and `widget_id` rename
   targets, all verified against the baseline before any worker saw them (`run-tests.sh`
   is hermetic — vendored pytest, no system install). Smoke passed 2026-11-11:
   `exit=0`, 12 s, 18 events, session dir created under the worker's cwd slug.
   Full batch passed 2026-11-11 — 6/6 `exit=0`, strictly one at a time, no timeout hit:

   | run | s | ev | grade vs seeded expectations |
   |---|---|---|---|
   | smoke | 12 | 18 | pass |
   | task1 grep+summarize | 68 | 23 | A — all 5 sites, file:line, pricing flagged |
   | task2 test triage | 306 | 44 | S — leap bug judged real, 308 independently recomputed; audit test proven a *deterministic* order dependency (sharper than the fixture's own "flaky" label); tree untouched as instructed |
   | task3 mechanical rename | 637 | 97 | S — 17 sites/4 files incl. JSON keys; docstrings/comments left; suite result identical to baseline (behavior-preserving) |
   | task4 doc from source | 399 | 113 | S — claims verified by running the classes; no-alias hard-break finding; w→p id translator proposed |
   | task5 find+fix bug | 84 | 65 | S — minimal 2-line fix using the module's own `is_leap`; target test flips green, unrelated failure left red |
   | approval_probe | 10 | 18 | verdict: `never` auto-approves — safety must come from worktree + sandbox flags, not the approval layer |

   Local Qwen over the DSH channel clears the gate: DSH stays the first adapter;
   phase 1 can start.
1. **Broker MVP, DSH adapter only**: daemon + shim, start/status/events/report/
   send(queued)/stop, worktrees, transcript mirror, notifications via the monitor, one
   concurrent worker. Exit criterion: Opus runs a 3-worker refactor batch unattended
   end-to-end, backgrounded, with takeover after a simulated orchestrator death.
2. **Swap proof**: Qwen Code adapter (daemon mode; tests live injection) and Codex
   adapter (tests the lowest-common-denominator path). A harness swap must not touch the
   MCP surface, the plugin, or any task text.
1.5. **DSH as second orchestrator** (user's ask, after 1): register the broker's MCP
   shim in DSH. Resolved from upstream docs (deepseek-harness repo,
   `packages/mcp/mcp-client/README.md`, `docs/user/guide/mcp-memory.md`): servers are
   declared by adding one `@deepseek-ai/dsh-mcp-client` entry per server to a profile's
   `cordis.patch.yml` — the same layer that already points headless at Strata — with
   `{serverName, transport: stdio, command, args, env}`; tools then appear as
   `mcp__<serverName>__<tool>`. `dsh-mcp-resources` (bundled everywhere **[V]**) only
   reads resources from already-declared servers; `dsh-mcp-client` itself is
   config-empty until a row is added. A `mcp-spike` profile copy with a stdio Python
   server parses clean in `--dump-config`, and the live round-trip passed 2026-11-11:
   a headless run called `mcp__spike__pong {"n":"42"}`, the 40-line stdlib server
   answered `PONG 42`, and the final event carried it verbatim (13 events, exit 0)
   **[V]**. Note: the stdio bridge scrubs `KEY|PASSWORD|SECRET|TOKEN`-style
   ambient vars from child envs; pass needed vars in the row's `config.env`.
   Then run the fleet e2e from inside a DSH GUI session:
   proves orchestrator-side harness neutrality (same MCP surface served two harnesses)
   and gives the human a real viewer: worker sessions live in the shared session store
   keyed by workspace, so the GUI may show them directly **[U spike]**; else the
   `transcript.jsonl` mirror is the fallback viewer.
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
- **DSH external session channel: resolved.** Headless `--session-id` resume +
  `--permission-mode`/`--approval-policy` leash + `--json` event stream, verified from
  the CLI's own help (2026-11-11). No need for the web-profile session API in phase 1.
- **DSH worker events + config: measured (smoke, 2026-11-11).** Session id in the first
  `--json` event; event vocabulary `session/status/thinking/tool_call/tool_result/text/
  final`; worker sessions keyed by cwd slug under `~/.dsh/sessions/`; headless needs the
  `llm-pi-ai` provider patch + `LOCAL_API_KEY` at spawn (from `strata launch --show-key`).
- **Phase 0 passed the quality gate (2026-11-11).** 6/6 runs exit 0; every task graded
  A or S against seeded expectations (scorecard in Phases §0); the local model handled
  triage *and* a correct minimal bug fix — DSH adapter first stands.
- **`--approval-policy never` = auto-approve**, not fail-closed (probe write to /tmp
  went through). Worker containment must be worktree discipline + sandbox-mode flags,
  not the approval layer.
- **DSH MCP declaration = one `@deepseek-ai/dsh-mcp-client` entry per server in the
  profile's `cordis.patch.yml`** (stdio: command/args/env; tools as
  `mcp__<server>__<tool>`); live-verified via the pong spike (2026-11-11). The broker
  shim will be served to DSH-side orchestrators through this same layer.

## Still open

1. **Monitor reliability**: confirm the Claude Code plugin monitor actually injects into
   a live session (docs list it as a component; behavior needs a phase-1 spike). Fallback
   is already designed: cursor polling.
2. **GUI viewer for headless worker sessions**: sessions are keyed by cwd slug, so a
   GUI session opened at a worker's worktree sees the same store; whether headless
   sessions render live in the web GUI is untested (phase-1.5 spike).
