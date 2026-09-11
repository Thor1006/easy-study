"""`gm` — the Glass Membrane command line.

    gm ui [--demo]                 open Silicate in your browser (starts the runtime host)
    gm serve [--demo]              run the host without opening a browser
    gm run "TEXT" [--demo]         submit a request and print the answer
    gm steer RUN "TEXT"            steer a running request
    gm cap RUN N                   change a run's agent limit while it runs
    gm cancel RUN                  cancel a run
    gm status [RUN]                show the host, providers, and runs
    gm replay [--demo]             rebuild state from the journal (read-only) and summarise it
    gm probe --confirm             measure how many parallel calls your plans sustain (uses quota)

The CLI talks to a running host when there is one, so steers from here and
from Silicate reach the same live run. `gm run` starts a private host in this
process when none is running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .runtime import data_dir_for

EXPERIMENT_NOTE = "experiment control (Arch §15) — not for normal use"


# ---------------------------------------------------------------------- host discovery / client
class Client:
    def __init__(self, info: dict) -> None:
        self.info = info
        self.base = f"http://127.0.0.1:{info['port']}"
        self.token = info["token"]

    def get(self, path: str, timeout: float = 10):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict, timeout: float = 900):
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "X-GM-Token": self.token})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise SystemExit(f"error: {detail}") from None

    def events(self, since: int = 0):
        with urllib.request.urlopen(f"{self.base}/api/events?since={since}", timeout=3600) as response:
            for raw in response:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("data: "):
                    yield json.loads(line[6:])


def find_host(demo: bool) -> Client | None:
    state_file = data_dir_for(demo) / "host.json"
    if not state_file.exists():
        return None
    try:
        client = Client(json.loads(state_file.read_text(encoding="utf-8")))
        client.get("/api/state", timeout=2)
        return client
    except (OSError, ValueError, KeyError):
        return None


def require_host(demo: bool) -> Client:
    client = find_host(demo) or find_host(not demo)
    if client is None:
        raise SystemExit("No Glass Membrane host is running. Start one with `gm ui` (or `gm serve`).")
    return client


# ---------------------------------------------------------------------- printing
def _event_line(event: dict, started: float) -> str | None:
    if not event.get("message"):
        return None
    return f"  {time.time() - started:6.1f}s  {event['type']:<18} {event['message']}"


def _print_result(run: dict) -> None:
    print()
    if run["status"] == "done":
        print(run.get("answer") or "")
        print()
        print(f"— {run.get('answered_by')} · {run.get('calls')} provider call(s) · run {run['id']}")
    else:
        print(f"Run {run['id']} ended: {run['status']} — {run.get('error') or ''}")


# ---------------------------------------------------------------------- commands
def cmd_ui(args, open_browser: bool = True) -> None:
    from .api import Host
    from .runtime import build_runtime

    existing = find_host(args.demo)
    if existing:
        print(f"Silicate is already running at {existing.base}/")
        if open_browser:
            webbrowser.open(existing.base + "/")
        return
    runtime = build_runtime(demo=args.demo)
    host = Host(runtime, port=args.port, state_file=data_dir_for(args.demo) / "host.json").start()
    mode = "DEMO MODE — simulated models, no provider usage" if args.demo else \
        "LIVE — requests use your Claude / Codex subscriptions"
    print(f"Silicate is running at {host.url}  ({mode})")
    print(f"Providers: {', '.join(runtime.adapters) or 'none'}   ·   press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(host.url)
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")


def cmd_run(args) -> None:
    client = find_host(args.demo)
    payload = {"text": args.text, "max_agents": args.max_agents, "source": "cli",
               "force_route": args.force_route, "filter_bypass": args.filter_bypass}
    if client:
        started = time.time()
        since = client.get("/api/state")["event_seq"]
        run = client.post("/api/runs", payload)["run"]
        print(f"Run {run['id']} submitted to {client.base}/ — steer it there or with `gm steer {run['id']} \"…\"`")
        try:
            for event in client.events(since):
                if event.get("run") != run["id"]:
                    continue
                if not args.quiet and (line := _event_line(event, started)):
                    print(line)
                if event["type"] in ("run_done", "run_failed", "CANCEL"):
                    break
        except KeyboardInterrupt:
            print(f"\nStopped watching; the run continues on the host. `gm status {run['id']}` to check it.")
            return
        _print_result(client.get(f"/api/runs/{run['id']}"))
        return

    from .runtime import build_runtime

    async def main():
        runtime = build_runtime(demo=args.demo)
        started = time.time()
        if not args.quiet:
            runtime.subscribe(lambda e: (line := _event_line(e, started)) and print(line, flush=True))
        await runtime.start()
        try:
            run = await runtime.submit(args.text, max_agents=args.max_agents, source="cli",
                                       force_route=args.force_route, filter_bypass=args.filter_bypass)
            await runtime.wait(run.id)
            return run.to_dict()
        finally:
            await runtime.stop()

    _print_result(asyncio.run(main()))
    print("Tip: `gm ui` opens Silicate, where you can watch and steer runs live.")


def cmd_steer(args) -> None:
    result = require_host(args.demo).post(f"/api/runs/{args.run}/steer", {"text": args.text, "source": "cli"})
    print(result.get("message") or json.dumps(result, indent=2))


def cmd_cap(args) -> None:
    result = require_host(args.demo).post(f"/api/runs/{args.run}/cap", {"n": args.n})
    print(f"Agent limit for {result['run']}: {result['previous']} → {result['max_agents']}")


def cmd_cancel(args) -> None:
    result = require_host(args.demo).post(f"/api/runs/{args.run}/cancel", {})
    print(f"Run {result['run']}: {result['status']}")


def cmd_status(args) -> None:
    client = require_host(args.demo)
    if args.run:
        detail = client.get(f"/api/runs/{args.run}")
        print(f"{detail['id']}  {detail['status']}  — {detail['status_line']}")
        print(f"answered by: {detail.get('answered_by')}   peak processes: {detail['peak_processes']}   "
              f"calls: {detail['calls']}")
        if detail.get("task"):
            print("\nnode                  phase      tier         validity  attempts  result")
            for node in detail["task"]["nodes"].values():
                print(f"{node['id'][:20]:<21} {node['phase']:<10} {node['tier']:<12} "
                      f"{str(node['validity'] or '-'):<9} {node['attempt']:<9} {node['result_ref'] or '-'}")
        totals = detail["ledger_totals"]
        print(f"\nledger: {totals['calls']} calls, {totals['input_tokens']} in / {totals['output_tokens']} out tokens"
              f" (+{totals['unknown_usage_calls']} calls with unknown usage)")
        return
    state = client.get("/api/state")
    print(f"Host {client.base}/  ·  {'demo' if state['demo'] else 'live'}  ·  v{state['version']}")
    for name, pool in state["providers"].items():
        print(f"  {name:<7} {pool['live']}/{pool['cap']} live (ceiling {pool['ceiling']})"
              + ("  backing off" if pool["backing_off"] else ""))
    print("\nrecent runs:")
    for run in state["runs"][:10]:
        print(f"  {run['id']}  {run['status']:<9} {str(run.get('answered_by') or run['status_line'])[:40]:<40} "
              f"{run['text'][:50]}")


def cmd_replay(args) -> None:
    from .journal import Journal
    from .state import StateManager

    data_dir = data_dir_for(args.demo)
    journal = Journal(data_dir / "state" / "journal" / "journal.jsonl", fsync=False)
    checkpoint = data_dir / "state" / "checkpoint" / "state.json"
    state = StateManager.recover(journal, checkpoint=str(checkpoint) if checkpoint.exists() else None)
    committed = journal.committed_transactions()
    print(f"Journal: {len(committed)} committed transactions, "
          f"{len(journal.incomplete_transactions())} incomplete attempt(s) ignored")
    print(f"Checkpoint: {'yes' if checkpoint.exists() else 'none'}; state at transaction {state.state['last_seq']}")
    for task in state.state["tasks"].values():
        rejections = sum(len(n["rejections"]) for n in task["nodes"].values())
        print(f"  {task['id']}  {task['status']:<11} v{task['version']}  {len(task['nodes'])} nodes  "
              f"{rejections} rejected result(s)  {task['goal'][:50]}")


def cmd_probe(args) -> None:
    from .probe import run_probe

    if not args.confirm:
        raise SystemExit("`gm probe` makes real provider calls and uses your subscription quota.\n"
                         "Re-run with --confirm to proceed, e.g.  gm probe --confirm --levels 1,3")
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    asyncio.run(run_probe(providers, levels))


# ---------------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gm", description="Glass Membrane runtime and Silicate UI")
    parser.add_argument("--demo", action="store_true", help="use simulated models (no provider usage)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("ui", "open Silicate in your browser"), ("serve", "run the host without a browser")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--port", type=int, default=0)
        p.add_argument("--demo", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("run", help="submit a request")
    p.add_argument("text")
    p.add_argument("--max-agents", type=int, default=None)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--force-route", action="store_true", help=f"filter may not answer — {EXPERIMENT_NOTE}")
    p.add_argument("--filter-bypass", action="store_true", help=f"skip the filter — {EXPERIMENT_NOTE}")
    p.add_argument("--demo", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("steer", help="steer a running request")
    p.add_argument("run")
    p.add_argument("text")
    p = sub.add_parser("cap", help="change a run's agent limit")
    p.add_argument("run")
    p.add_argument("n", type=int)
    p = sub.add_parser("cancel", help="cancel a run")
    p.add_argument("run")
    p = sub.add_parser("status", help="show host, providers, and runs")
    p.add_argument("run", nargs="?")
    p = sub.add_parser("replay", help="rebuild state from the journal (read-only)")
    p.add_argument("--demo", action="store_true", default=argparse.SUPPRESS)
    p = sub.add_parser("probe", help="measure parallel capacity (uses quota)")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--levels", default="1,3,6,12")
    p.add_argument("--providers", default="claude,codex")
    return parser


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    commands = {"ui": lambda a: cmd_ui(a, True), "serve": lambda a: cmd_ui(a, False), "run": cmd_run,
                "steer": cmd_steer, "cap": cmd_cap, "cancel": cmd_cancel, "status": cmd_status,
                "replay": cmd_replay, "probe": cmd_probe}
    commands[args.command](args)


if __name__ == "__main__":
    main()
