# Glass-membrane project

**Glass Membrane** is a model-agnostic, adaptive AI runtime. **Silicate** is its operator UI.

Glass Membrane routes each request through an eight-slot front-end filter, and, when the filter can't simply answer it, schedules the work across twelve logical reasoning cores backed by the **Claude Code** and **Codex** CLIs. The runtime — not any model — owns task state, scheduling, leases, and final acceptance of results.

> **Status:** v1 working kernel. It is implemented and tested against simulated models; it is **not** benchmarked, and no performance claims are made. The design is specified in the three documents below; implementation choices are recorded in [`docs/IMPLEMENTATION_DECISIONS.md`](docs/IMPLEMENTATION_DECISIONS.md).

| Document | What it is |
|---|---|
| [`Glass_Membrane_Architecture.md`](Glass_Membrane_Architecture.md) | The canonical architecture |
| [`GLASS_MEMBRANE_FULL_SCHEMATIC.md`](GLASS_MEMBRANE_FULL_SCHEMATIC.md) | Components, data flow, correctness scenarios |
| [`CLAUDE_AGENT_OPERATING_INSTRUCTIONS.md`](CLAUDE_AGENT_OPERATING_INSTRUCTIONS.md) | How models in runtime roles must behave |

## Try it (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\gm.exe --demo ui
```

`--demo` uses simulated models, so nothing is sent to any provider. Silicate opens in your browser. Try `2x2` (answered by the filter with one process), then `Compare 3 sorting algorithms for nearly-sorted data`, and steer it while it runs.

### Live mode

Live mode uses your own subscriptions through the installed CLIs:

- `claude` — Claude Code, signed in with your Claude plan
- `codex` — Codex CLI, signed in with ChatGPT (`codex login status`)

```powershell
.\.venv\Scripts\gm.exe ui
```

Every live call counts against your plan's usage limits. The runtime deliberately strips `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from child processes so calls use your subscription login rather than an API key.

## Commands

| Command | What it does |
|---|---|
| `gm ui` / `gm serve` | Start the runtime host (with or without opening Silicate) |
| `gm run "…"` | Submit a request and print the answer |
| `gm steer RUN "…"` | Steer a running request |
| `gm cap RUN N` | Change a running request's agent limit |
| `gm cancel RUN` | Cancel a run |
| `gm status [RUN]` | Show providers, runs, and a run's task graph |
| `gm replay` | Rebuild state from the journal, read-only |
| `gm probe --confirm` | Measure how many parallel calls your plans sustain (uses quota) |

Put `--demo` before the subcommand for simulated models (`gm --demo run "2x2"`).

## How a request flows

1. **Input Gateway → filter.** A filter slot either answers (easy requests), routes (a packet describing difficulty, parallelism, and needs), or classifies the input as a steer for running work. Low confidence escalates efficiency → performance → flagship slots, one at a time.
2. **Kernel.** Routed work becomes a task graph. The scheduler assigns ready nodes to idle cores with a focus contract and a bounded lease, choosing a provider binding that has capacity.
3. **Validated commits.** Every result is stored immutably and accepted only if its lease is live, its state version is current, and its inputs haven't changed. Results computed against an obsolete state are rejected and the node is re-run.
4. **Steering.** A steer commits a new constraint version, revokes affected leases, re-runs affected nodes, and keeps unaffected results.
5. **Output assembly** presents the accepted results with any material uncertainty.

Concurrency is elastic: a process exists only while a call runs. `2x2` uses one process; a wide task can use up to 12 per provider (configurable in [`runtime/config/models.toml`](runtime/config/models.toml)).

## Code map

| Spec | Module |
|---|---|
| References, store, journal (§10, §12, §13, §17) | `refs.py`, `store.py`, `journal.py` |
| State Manager, leases, blackboard, focus contracts (§8, §12) | `state.py`, `leases.py`, `blackboard.py`, `focus.py` |
| Typed fabric and mailboxes (§9) | `events.py`, `fabric.py` |
| Gateway, 8-slot filter, Interrupt Controller (§4, §11) | `gateway.py`, `router.py`, `interrupt.py` |
| Scheduler, cores, pools, dynamic shifting (§5, Ops §4–6) | `scheduler.py`, `cores.py`, `pools.py` |
| Swarm Governor (§7) | `swarm.py` |
| Context service, services, assembler, budget, permissions | `context.py`, `services.py`, `assembler.py`, `budget.py`, `permissions.py` |
| Watchdog, Token Ledger, registry (§6, §13, §14) | `watchdog.py`, `ledger.py`, `registry.py` |
| Model adapters (§6) | `adapters/claude_code.py`, `adapters/codex.py`, `adapters/fake.py` |
| Runtime host, API, CLI, Silicate | `runtime.py`, `api.py`, `cli.py`, `silicate/` |
| Role instructions (Ops §7) | `runtime/skills/*.md` |
| Claude Code skill | `.claude/skills/glass-membrane/SKILL.md` |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

All tests use simulated models. They cover the correctness scenarios in Schematic §15 that v1 implements — a steer racing old results, a worker finishing after its lease expired, saturated queues, a cancelled swarm parent, a crash after a journal write, a lost tool response, and re-binding after a provider failure — plus elastic scaling, the filter cascade, and the Silicate API.

## Not in v1

DEBUG / SHADOW / OVERDRIVE modes, the developer control plane, telemetry beyond the Token Ledger, and the runtime evaluation harness. See the architecture's §16–§18.
