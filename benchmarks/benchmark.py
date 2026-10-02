"""Paired real Codex turns: Jevex routing versus a fixed Sol session."""

import ast
import builtins
import copy
import json
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jevex
from report import render

HERE = Path(__file__).resolve().parent
BASELINE = "gpt-6.1-sol"
JEV_INPUT_RATE = 0.042  # USD / 1M, TypeSafe's published input rate
PRICES = {  # USD / 1M, standard short-context API list prices on 2026-10-02
    "gpt-6-luna": {"input": .10, "cached": .01, "write": .125, "output": .50},
    "gpt-6-sol": {"input": 2.00, "cached": .20, "write": 2.50, "output": 10.00},
    "gpt-6.1-sol": {"input": 2.00, "cached": .10, "write": 2.50, "output": 10.00},
}
TASKS = [
    {"id": "slug", "name": "Slug cleanup", "function": "slugify",
     "prompt": "Write one Python function named slugify(text). Input is ASCII. Lowercase letters, keep letters and digits, replace each run of non-alphanumeric characters with one hyphen, and remove leading or trailing hyphens. No imports. Reply with only function source, no markdown. Do not use tools.",
     "cases": [(("Hello, World!",), "hello-world"), (("  A__B  ",), "a-b"),
               (("123---Go",), "123-go"), (("!!!",), "")]},
    {"id": "csv", "name": "CSV field parsing", "function": "split_csv_row",
     "prompt": "Write one Python function named split_csv_row(line) that parses a single CSV row into a list of strings. Commas separate fields. Double quotes around a field allow commas inside it; two double quotes inside a quoted field mean one literal quote. Preserve spaces and empty fields. There are no embedded newlines. Raise ValueError on an unclosed quoted field. No imports. Reply with only function source, no markdown. Do not use tools.",
     "cases": [(("a,b,c",), ["a", "b", "c"]), (("\"a,b\",c",), ["a,b", "c"]),
               (("\"a\"\"b\",",), ['a"b', ""]), ((",,",), ["", "", ""]),
               (("\"open",), ValueError)]},
    {"id": "dag", "name": "Deterministic task scheduling", "function": "schedule_tasks",
     "prompt": "Write one Python function named schedule_tasks(deps). deps maps task names to lists of prerequisite task names. Return a valid topological ordering; whenever several tasks are ready, choose the lexicographically smallest. Raise ValueError for an unknown prerequisite or a cycle. Do not mutate deps. No imports. Reply with only function source, no markdown. Do not use tools.",
     "cases": [(({"b": [], "a": []},), ["a", "b"]),
               (({"build": ["lint", "test"], "test": [], "lint": []},), ["lint", "test", "build"]),
               (({"a": ["missing"]},), ValueError), (({"a": ["b"], "b": ["a"]},), ValueError),
               (({},), [])]},
    {"id": "expr", "name": "Untrusted expression parser", "function": "evaluate_expression",
     "prompt": "Security-sensitive coding task: implement one Python function evaluate_expression(text) for untrusted input. Accept decimal integers, whitespace, binary + - *, unary + -, and parentheses. Respect precedence and return an int. Reject any other character, missing operand, or unbalanced parenthesis with ValueError. Never use eval, exec, or imports. Reply with only function source, no markdown. Do not use tools.",
     "cases": [(("1+2*3",), 7), ((("(1+2)*3",)), 9), (("-2*3",), -6),
               (("1--2",), 3), ((" 42 ",), 42), (("1+",), ValueError),
               (("(1+2",), ValueError), (("2(3)",), ValueError),
               (("__import__('os')",), ValueError), (("",), ValueError)]},
]


def delta(raw, previous):
    """Codex 0.159 reports cumulative session usage in turn.completed."""
    return {key: value - previous.get(key, 0) if value >= previous.get(key, 0) else value
            for key, value in raw.items()}


def grade(task, answer):
    answer = answer.strip()
    if answer.startswith("```") and answer.endswith("```"):
        answer = "\n".join(answer.splitlines()[1:-1])
    try:
        tree = ast.parse(answer)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != task["function"]:
            return 0, "not a single requested function"
        if any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef))
               for node in ast.walk(tree)):
            return 0, "disallowed import or scope"
        names = ("all", "any", "bool", "dict", "enumerate", "int", "isinstance", "len", "list",
        "max", "min", "ord", "range", "reversed", "set", "sorted", "str", "tuple", "ValueError", "zip")
        namespace = {"__builtins__": {name: getattr(builtins, name) for name in names}}
        def expired(*_):
            raise TimeoutError("grader deadline")
        old_handler = signal.signal(signal.SIGALRM, expired)
        signal.alarm(3)
        try:
            exec(compile(tree, "<answer>", "exec"), namespace)
            function = namespace[task["function"]]
            passed = 0
            for args, expected in task["cases"]:
                try:
                    actual = function(*copy.deepcopy(args))
                    passed += expected is not ValueError and actual == expected
                except ValueError:
                    passed += expected is ValueError
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        return passed, ""
    except Exception as exc:
        return 0, type(exc).__name__


def estimated_cost(model, usage):
    rates = PRICES.get(model)
    if not rates or not usage:
        return None
    cached = usage.get("cached_input_tokens", 0)
    writes = usage.get("cache_write_input_tokens", 0)
    uncached = max(0, usage.get("input_tokens", 0) - cached - writes)
    return (uncached * rates["input"] + cached * rates["cached"] + writes * rates["write"]
            + usage.get("output_tokens", 0) * rates["output"]) / 1_000_000


def codex_turn(task, model, session, cwd, previous):
    command = ["codex", "exec"] + (["resume"] if session else [])
    command += ["--json", "--skip-git-repo-check", "--model", model,
                "-c", 'sandbox_mode="read-only"']
    if session:
        command.append(session)
    command.append(task["prompt"])
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=180)
    seconds = round(time.monotonic() - started, 2)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    actual_session = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    raw = next((e.get("usage", {}) for e in reversed(events) if e.get("type") == "turn.completed"), {})
    usage = delta(raw, previous)
    answer = next((e.get("item", {}).get("text", "") for e in reversed(events)
                   if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"), "")
    passed, issue = grade(task, answer) if answer else (0, "no answer")
    if result.returncode or not actual_session or (session and actual_session != session):
        passed, issue = 0, f"Codex exit {result.returncode}; session continuity failed" if session and actual_session != session else f"Codex exit {result.returncode}"
    record = {"task": task["id"], "model": model, "seconds": seconds, "usage": usage,
              "passed": passed, "checks": len(task["cases"]), "issue": issue,
              "cost_usd": estimated_cost(model, usage),
              "tool_calls": sum(e.get("type") == "item.started" and e.get("item", {}).get("type") == "command_execution" for e in events),
              "codex_warnings": sum("failed to record rollout items" in line for line in result.stderr.splitlines())}
    return actual_session, raw, record


def main():
    extending = len(sys.argv) == 4 and sys.argv[1] == "--extend"
    if len(sys.argv) > 1 and not extending:
        raise SystemExit("usage: benchmark.py [--extend ADAPTIVE_SESSION FIXED_SESSION]")
    if not jevex.load_key():
        raise RuntimeError("Jev API key required for the routed arm")
    started = time.monotonic()
    models = jevex.available_models()
    catalog_seconds = round(time.monotonic() - started, 2)
    settings = jevex.load_settings()
    if not settings or BASELINE not in models:
        raise RuntimeError("Review models in Jevex and ensure the Sol baseline is available")
    data = (json.loads((HERE / "results.json").read_text()) if extending else
            {"created_utc": datetime.now(timezone.utc).isoformat(),
             "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
             "catalog_seconds": catalog_seconds, "baseline": BASELINE,
             "eligible_models": sorted(set(models) - settings["excluded_models"]),
             "pricing": PRICES, "jev_input_rate": JEV_INPUT_RATE, "strategies": []})
    with tempfile.TemporaryDirectory(prefix="jevex-benchmark-") as cwd:
        for index, name in enumerate(("Jevex", "Fixed Sol")):
            strategy = data["strategies"][index] if extending else {"name": name, "turns": []}
            if not extending:
                data["strategies"].append(strategy)
            session = sys.argv[index + 2] if extending else None
            previous = {key: sum(t["usage"].get(key, 0) for t in strategy["turns"])
                        for key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")} if extending else {}
            print(f"\n{name}", flush=True)
            for task in TASKS[len(strategy["turns"]):]:
                model, source, router_seconds, router_usage = BASELINE, "fixed", 0, {}
                if name == "Jevex":
                    start = time.monotonic()
                    model, source, _ = jevex.route(task["prompt"], models=models, on_usage=router_usage.update)
                    router_seconds = round(time.monotonic() - start, 2)
                if model not in PRICES:
                    raise RuntimeError(f"No verified price for routed model {model}; stop before spending Codex tokens")
                print(f"  {task['name']}: {model} ...", end=" ", flush=True)
                actual_session, raw, turn = codex_turn(task, model, session, cwd, previous)
                turn.update({"source": source, "router_seconds": router_seconds, "router_usage": router_usage,
                             "router_cost_usd": router_usage.get("input_tokens", 0) * JEV_INPUT_RATE / 1_000_000})
                strategy["turns"].append(turn)
                print(f"{turn['passed']}/{turn['checks']} checks · {turn['seconds']:.1f}s", flush=True)
                (HERE / "results.json").write_text(json.dumps(data, indent=2) + "\n")
                if not actual_session or (session and actual_session != session):
                    break
                session, previous = actual_session, raw
    (HERE / "report.html").write_text(render(data, TASKS))
    print(f"\nReport: {HERE / 'report.html'}", flush=True)


if __name__ == "__main__":
    main()
