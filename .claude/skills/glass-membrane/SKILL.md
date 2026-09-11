---
name: glass-membrane
description: Run a request through the Glass Membrane runtime in this repository (8-slot filter plus 12 logical cores on Claude Code and Codex), steer or re-limit a running request, check its status, or open the Silicate UI. Use when the user says "use glass membrane", "run this through gm", "open silicate", or asks to steer, cap, cancel, or check a Glass Membrane run.
---

# Glass Membrane

Design documents, in reading order: `Glass_Membrane_Architecture.md` → `GLASS_MEMBRANE_FULL_SCHEMATIC.md` → `CLAUDE_AGENT_OPERATING_INSTRUCTIONS.md`. Implementation choices are recorded separately in `docs/IMPLEMENTATION_DECISIONS.md`.

The `gm` command is `.venv\Scripts\gm.exe` in this repository (or `python -m glass_membrane`).

## Running a request

1. **Pass the user's request to `gm run` verbatim** — their exact words, in quotes:

   ```
   .venv\Scripts\gm.exe run "<the user's request, unchanged>"
   ```

   Do not reword, summarize, split, translate, or decorate it. Do not add instructions about effort, thoroughness, reasoning style, or how many agents to involve. The front-end filter judges difficulty from the request itself; extra wording changes that judgement.

2. **Let the filter decide.** Easy requests (for example "2x2") are answered directly by a front-end filter slot using one process and zero reasoning cores. An `answered_by: filter · …` line is the expected, correct outcome. Do not re-run the request to "get a better answer", do not answer it yourself instead of the runtime, and do not second-guess the filter's answer.

3. **Relay the result as returned**, followed by its `answered_by` line. If the run failed, report the failure code and message exactly as given.

Live runs use the owner's Claude and Codex subscriptions. Add `--demo` (before the subcommand: `gm --demo run "…"`) for simulated models when the user is exploring or testing. Ask before starting a live run the user did not explicitly request.

## While a run is going

These need a running host (`gm ui` or `gm serve`):

- Steer it with the user's words: `.venv\Scripts\gm.exe steer <run-id> "<the user's words>"`
- Change its agent limit: `.venv\Scripts\gm.exe cap <run-id> <n>` (lowering never stops work already running)
- Check it: `.venv\Scripts\gm.exe status <run-id>`
- Cancel it: `.venv\Scripts\gm.exe cancel <run-id>`

## The UI

`.venv\Scripts\gm.exe ui` opens Silicate in the browser (`gm --demo ui` for simulated models). Suggest it when the user wants to watch or steer work visually.

## Do not

- Pass the runtime's experiment controls (the Arch §15 flags that force or bypass the filter). They exist for evaluating the runtime, not for normal requests.
- Run `gm probe` unless the user asks for it; it deliberately uses subscription quota.
- Edit the three design documents to match the code. Report a discrepancy to the user instead.
