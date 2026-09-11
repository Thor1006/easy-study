# Silicate desktop and illustrated answers

Implemented 2026-09-11. This is a UI extension of the existing v1 runtime.

## Launch and lifecycle

Use `Start Silicate Desktop.cmd` (live) or `Start Silicate Desktop (demo).cmd`.
Both launch `python -m glass_membrane.desktop` from the project root. The launcher
uses the project environment, creating one when absent, and checks Tcl/Tk support.
`gm desktop` is also supported. No additional GUI dependencies are introduced.

The client reuses a same-mode local API host when present, or owns a newly started
host. Closing a client does not close a pre-existing host. Closing an owned host
stops its runtime, with a confirmation if known work remains active. Network calls
run on background threads; Tk widgets are updated only on the Tk event thread.

Question history reflects the current host's retained runs; this does not introduce
cross-restart chat-history persistence. The existing runtime still determines task
and steering semantics. UI changes do not add autonomous permissions or new modes.

## Illustration protocol

Provider role instructions now document a fenced `silicate` JSON block inside the
existing answer string. Existing strict provider response schemas stay compatible.
`Run.to_dict()` adds validated `answer_parts` for clients; the raw answer remains
available for the CLI and Markdown export. Both direct-router and worker answers
use the same parser. Final synthesis can emit its own visual from accepted findings.

Supported illustrations: signed horizontal bar charts, ordered step diagrams, and
comparison tables. The backend limits visual count, rows/steps, labels, numbers,
and payload size. Malformed blocks fall back to text. Frontend elements are built
using fixed SVG/DOM or native Tk widgets; no model-authored code is executed.
Neither arbitrary image loading nor photorealistic generation is implemented.

Captions must state units and distinguish illustrative values from measured data.
Those claims still depend on model/source quality; rendering validation does not
verify the truth of a chart. Browser charts expose their full values as an accessible
data disclosure. Copy/save preserves the original answer and structured visual data.

## Design

Desktop uses standard ttk frames, buttons, checkboxes, spinboxes, tabs and a
Treeview question list, with the Windows native theme when available. Only multiline
text and drawing surfaces use classic Tk widgets, because ttk has no equivalents.
The browser keeps its earlier warm palette, map, team controls and timeline.
Desktop groups operational detail into an Activity tab.

## Owner override version

Launch `Start Silicate Override.cmd` or `Start Silicate Override (demo).cmd`.
Equivalent commands: `python -m glass_membrane.override_desktop` and the same with
`--demo`. The owner console creates a **separate runtime session**; it does not
attach to or modify a normal browser/desktop session that is already running.

The Owner controls tab provides:

- Complete current configuration as editable JSON: filter thresholds, ordered
  tier/model/effort bindings, provider process ceilings, budgets, timeouts, lease
  duration, retry limits, context size, heartbeat, watchdog interval, and swarm policy.
- Apply configuration when idle, load startup values, and import/export profiles.
  Profiles load as drafts and take effect only after Apply session.
- Pause/resume dispatch on the selected routed run. In-flight calls can finish;
  this is not a freeze of a provider's internal generation.
- Revise/requeue a reasoning node: tier, focus instructions, optional provider/model/
  effort binding, and optional preferred logical core. Empty fields restore automatic
  selection. Refresh state and select a node before editing it.
- Enable/disable future assignments to a reasoning core. Current work can finish;
  at least one core must remain enabled. Clear explicit assignments before disabling
  their selected core.
- Force reasoning or bypass the routing filter for the next submitted question.
- State, core/provider snapshots, mailbox contents/counts/capacities, audit inspection,
  snapshot export and explicit checkpoints. Use Refresh state for a new snapshot;
  configuration and instruction drafts are not overwritten automatically.

The override desktop also includes a **Data flow** tab, updated automatically by
the selected run's normal polling. It shows input/filter attempts, planner dispatch,
parallel task dependencies, status, retries and completion. Click a node or select
it from the keyboard-accessible node list to inspect binding, lease, input/result
references, validity and rejection history. Zoom and two-axis scrollbars support
larger graphs. The map is read-only and clears when starting a new question.
Preset-question buttons have been removed from both desktop versions.

Node revisions validate the inspected task version, revoke affected leases, and
requeue the node and its dependents transactionally. Results from superseded work
remain subject to normal acceptance checks. Overrides do not mutate shared model
memory or grant arbitrary permissions to model workers. No owner mutation endpoint
is added to the normal HTTP API; controls run through the owning desktop process.

Changes are session scoped. Each session stores its journal/checkpoints/audit in
`runtime/owner-sessions/<session-id>/` (under `runtime/demo/` for demo mode). Settings
do not overwrite `models.toml`. Export a profile if you want to reuse settings.
Provider command changes require editing `models.toml` and restarting, since adapter
process configuration is established at startup. A larger ceiling remains subject
to provider availability and existing backoff. Model/effort support is provider
dependent and failures are reported normally.

This covers the implemented runtime's controls. Hidden provider model memory, raw
canonical-state editing, permission-table rewriting, and future architectural modes
are not adjustable here. Mailboxes are inspectable, not destructively drained.

## Verification

The simulated suite covers invalid/nonfinite/mismatched illustration payloads,
text fallback, ordering and limits, prompt/CLI integration, and an API round trip
from a simulated model to a structured illustration. A native widget smoke test
renders all supported illustration types and plain answers. Existing kernel/API
tests continue to cover steering, cancellation, and state validation.

Live provider behavior has not been exercised for this change; no subscription
quota was used. Models may omit illustrations or produce invalid blocks, in which
case the textual answer remains available.
