"""Route each user turn with Jev, then let Codex own the conversation."""

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

JEV_URL = "https://api.typesafe.ai/v1/systemone"


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jevex" / "config.json"


def key_path():
    return config_path().with_name("key")


def usage_path():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "jevex" / "usage.jsonl"


def usage_records():
    path = usage_path()
    if not path.exists():
        return []
    with path.open() as file:
        records = [json.loads(line) for line in file if line.strip()]
    totals = {}
    for record in records:
        session, reported = record.get("session"), record["usage"]
        previous = totals.get(session, {}) if session else {}
        record["usage"] = {key: value - previous.get(key, 0) if value >= previous.get(key, 0) else value
                           for key, value in reported.items()}
        if session and reported:
            totals[session] = reported
    return records


def save_usage(record):
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(json.dumps(record) + "\n")


def print_stats():
    records = [r for r in usage_records() if r["usage"].get("input_tokens")]
    if not records:
        print("No Codex turns recorded yet.")
        return
    print("Codex-reported usage (cached input is included in input tokens)")
    for label, group in (("same model", [r for r in records if r["previous_model"] == r["model"]]),
                         ("model switch", [r for r in records if r["previous_model"] and r["previous_model"] != r["model"]]),
                         ("first turn", [r for r in records if not r["previous_model"]])):
        if not group:
            continue
        input_tokens = sum(r["usage"].get("input_tokens", 0) for r in group)
        cached = sum(r["usage"].get("cached_input_tokens", 0) for r in group)
        seconds = sum(r["seconds"] for r in group)
        print(f"{label:12} {len(group):4} turns  {cached / input_tokens:.0%} cached input" if input_tokens else
              f"{label:12} {len(group):4} turns  cache rate unavailable", end="")
        print(f"  {seconds / len(group):.1f}s average  {input_tokens:,} input tokens")
    print(f"Local log: {usage_path()}")


def load_key():
    return os.environ.get("JEV_API_KEY") or (key_path().read_text().strip() if key_path().exists() else None)


def save_key(key):
    key = key.strip()
    if not key:
        raise ValueError("Jev API key cannot be empty")
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(key + "\n")


def load_settings():
    path = config_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    excluded = data.get("excluded_models")
    known = data.get("known_models")
    if not all(isinstance(items, list) and all(isinstance(model, str) for model in items) for items in (excluded, known)):
        raise ValueError(f"Invalid Jevex settings in {path}")
    return {"excluded_models": set(excluded), "known_models": set(known)}


def save_settings(excluded, known):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"excluded_models": sorted(excluded), "known_models": sorted(known)}, indent=2) + "\n")


def available_models():
    """Read the visible model catalog from the installed Codex app-server."""
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    buffer = b""

    def send(message):
        process.stdin.write(json.dumps(message).encode() + b"\n")
        process.stdin.flush()

    def response(request_id):
        nonlocal buffer
        deadline = time.monotonic() + 30
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = json.loads(line)
                    if message.get("id") == request_id:
                        if "error" in message:
                            raise RuntimeError(message["error"].get("message", "Codex catalog error"))
                        return message["result"]
                if selector.select(max(0, deadline - time.monotonic())):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    buffer += chunk
        raise RuntimeError("Codex model catalog did not respond")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "jevex-cli", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        }})
        response(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "model/list", "params": {"limit": 100}})
        data = response(2)["data"]
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    models = {item.get("model") or item["id"]: item for item in data if not item.get("hidden")}
    if not models:
        raise RuntimeError("Codex returned no visible models")
    return models


def choose_model(prompt, key, models, url=JEV_URL):
    """Ask Jev one typed question. Unexpected replies are errors, not routes."""
    if not key:
        raise ValueError("Set JEV_API_KEY or use --model.")
    body = {
        "model": "jev-latest",
        # ponytail: The first 12k characters keep routing cheap; expand this if long prompts misroute.
        "state": prompt[:12000],
        "questions": {
            "tier": {
                "type": "choice",
                "instructions": "Choose the least costly available Codex model likely to complete this user turn correctly. Judge the actual work, not prompt length. Prefer stronger models for high-stakes or deeply ambiguous work. Use model names and descriptions to infer capability and cost; do not choose a specialty model for unrelated work.",
                "criteria": {model: f"{item.get('displayName', model)} — {item.get('description', '')}" for model, item in models.items()},
            }
        },
    }
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15) as response:
        answer = json.load(response)["answers"]["tier"]
    model = answer["choice"]
    if answer.get("type") != "choice" or model not in models:
        raise ValueError("Jev returned an invalid model choice")
    return model


def codex_command(prompt, model, session=None):
    command = ["codex", "exec"]
    if session:
        command += ["resume"]
    command += ["--json", "--skip-git-repo-check", "--model", model]
    if session:
        command += [session]
    command += [prompt]
    return command


def run_codex(prompt, model, session=None, on_event=None, source="manual"):
    """Stream Codex JSON events and return its session ID and exit code."""
    command = codex_command(prompt, model, session)
    previous_turn = next((r for r in reversed(usage_records()) if r["session"] == session and r["usage"]), None) if session else None
    previous_model = previous_turn["model"] if previous_turn else None
    started = time.monotonic()
    usage = {}
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        thread_id = session
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if on_event and line.strip():
                    on_event({"type": "jevex.stderr", "message": line.strip()})
                continue
            if event.get("type") == "thread.started":
                started_id = event.get("thread_id", thread_id)
                if session and started_id != session:
                    process.terminate()
                    raise RuntimeError(f"Codex resumed a different session ({started_id}); original session {session} was not resumed")
                thread_id = started_id
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
            if on_event:
                on_event(event)
        code = process.wait()
    save_usage({"session": thread_id, "model": model, "previous_model": previous_model,
                "source": source, "usage": usage, "seconds": round(time.monotonic() - started, 2),
                "exit_code": code})
    if on_event:
        on_event({"type": "jevex.usage", "record": usage_records()[-1],
                  "previous_cached": previous_turn["usage"].get("cached_input_tokens", 0) if previous_turn else None})
    return thread_id, code


def route(prompt, forced=None, models=None):
    models = models or available_models()
    if forced:
        if forced not in models:
            raise ValueError(f"{forced} is not in Codex's visible model catalog")
        return forced, "manual", models
    settings = load_settings()
    if settings is None:
        raise ValueError("Run jevex in a terminal once to choose eligible models")
    if set(models) - settings["known_models"]:
        raise ValueError("Codex has new models; run jevex in a terminal to review them")
    eligible = {model: item for model, item in models.items() if model not in settings["excluded_models"]}
    if not eligible:
        raise ValueError("Every available model is excluded; press Ctrl+M in jevex to change this")
    try:
        return choose_model(prompt, load_key(), eligible), "jev", models
    except Exception as exc:
        fallback = next((model for model, item in eligible.items() if item.get("isDefault")), next(iter(eligible)))
        return fallback, f"Fallback · Jev unavailable: {exc}", models


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jev-powered Codex CLI")
    parser.add_argument("prompt", nargs="*", help="Run one turn; omit for the interactive TUI")
    parser.add_argument("--session", help="Resume this Codex session ID")
    parser.add_argument("--model", help="Use an available Codex model without Jev")
    parser.add_argument("--dry-run", action="store_true", help="Show the route without starting Codex")
    parser.add_argument("--stats", action="store_true", help="Show local cache and latency statistics")
    args = parser.parse_args(argv)
    if args.stats:
        print_stats()
        return 0
    if not args.prompt and not args.dry_run and sys.stdin.isatty() and sys.stdout.isatty():
        from jevex_tui import JevexApp

        JevexApp(args.session, args.model).run()
        return 0
    prompt = " ".join(args.prompt) if args.prompt else sys.stdin.read().strip()
    if not prompt:
        parser.error("provide a prompt or run in a terminal for the TUI")
    try:
        model, source, _ = route(prompt, args.model)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"jevex · {model} · {source}", file=sys.stderr)
    if args.dry_run:
        return 0

    def show(event):
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                print(item.get("text", ""), flush=True)
        elif event.get("type") == "jevex.stderr":
            print(event["message"], file=sys.stderr)

    try:
        session, code = run_codex(prompt, model, args.session, show, source)
    except FileNotFoundError:
        parser.error("Codex CLI is not installed or is not on PATH")
    if session:
        print(f"Session: {session}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
