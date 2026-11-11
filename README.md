# spielmaster

Experiments in agent orchestration and harness plumbing: local worker agents (DSH,
Qwen Code, Codex CLI, Goose) on self-hosted models, driven by subscription-native
orchestrators like Claude Code. No routers in front of the orchestrator; the workers
run on [Strata](https://github.com/Niko1221/Strata)-served models over local APIs.

Start with the design docs in [`docs/`](docs/):

- [`docs/worker-broker-design.md`](docs/worker-broker-design.md) — a broker that owns
  worker sessions (start, queue, status, report, escalate) while the executing harness
  stays swappable. Claude Code orchestrates via MCP + plugin; workers run in their own
  git worktrees and report back.

Prototype code goes under `integrations/`.
