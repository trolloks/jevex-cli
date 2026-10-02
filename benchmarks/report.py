"""Render the benchmark's self-contained HTML report."""

from html import escape


def render(data, tasks):
    adaptive, fixed = data["strategies"]

    def total(strategy):
        turns = strategy["turns"]
        return {
            "cost": sum((t["cost_usd"] or 0) + t["router_cost_usd"] for t in turns),
            "seconds": sum(t["seconds"] + t["router_seconds"] for t in turns),
            "passed": sum(t["passed"] for t in turns),
            "checks": sum(t["checks"] for t in turns),
            "input": sum(t["usage"].get("input_tokens", 0) for t in turns),
            "cached": sum(t["usage"].get("cached_input_tokens", 0) for t in turns),
            "jev_tokens": sum(t["router_usage"].get("input_tokens", 0) for t in turns),
        }

    a, b = total(adaptive), total(fixed)
    savings = (b["cost"] - a["cost"]) / b["cost"] if b["cost"] else 0
    outcome = ("Small cost win; more runs needed" if a["passed"] == b["passed"] and savings > 0 else
               "Savings with a quality tradeoff" if savings > 0 else "No cost win in this pilot")
    max_cost = max(a["cost"], b["cost"], 0.000001)

    def bar(strategy, stats, color):
        width = 100 * stats["cost"] / max_cost
        return (f'<div class="bar-row"><div class="bar-label">{escape(strategy)}</div>'
                f'<div class="track"><div class="fill {color}" style="width:{width:.1f}%"></div></div>'
                f'<div class="bar-value">${stats["cost"]:.4f}</div></div>')

    rows = []
    for index, task in enumerate(tasks):
        for strategy in (adaptive, fixed):
            if index >= len(strategy["turns"]):
                continue
            turn = strategy["turns"][index]
            usage = turn["usage"]
            cache_rate = usage.get("cached_input_tokens", 0) / usage.get("input_tokens", 1) if usage.get("input_tokens") else 0
            cost = (turn["cost_usd"] or 0) + turn["router_cost_usd"]
            score_class = "pass" if turn["passed"] == turn["checks"] else "fail"
            rows.append(f'<tr><td class="task">{escape(task["name"])}</td><td>{escape(strategy["name"])}</td>'
                        f'<td><code>{escape(turn["model"])}</code></td><td class="{score_class}">{turn["passed"]}/{turn["checks"]}</td>'
                        f'<td>{cache_rate:.0%}</td><td>{turn["seconds"] + turn["router_seconds"]:.1f}s</td><td>${cost:.4f}</td></tr>')
    route = " <span class='arrow'>→</span> ".join(f'<code>{escape(t["model"])}</code>' for t in adaptive["turns"])
    model_switches = sum(adaptive["turns"][i]["model"] != adaptive["turns"][i-1]["model"] for i in range(1, len(adaptive["turns"])))
    before_switch = sum((t["cost_usd"] or 0) + t["router_cost_usd"] for t in adaptive["turns"][:-1])
    before_fixed = sum(t["cost_usd"] or 0 for t in fixed["turns"][:-1])
    switched = adaptive["turns"][-1]
    fixed_last = fixed["turns"][-1]
    switched_cache = switched["usage"]["cached_input_tokens"] / switched["usage"]["input_tokens"]
    fixed_last_cache = fixed_last["usage"]["cached_input_tokens"] / fixed_last["usage"]["input_tokens"]
    a_cache = a["cached"] / a["input"] if a["input"] else 0
    b_cache = b["cached"] / b["input"] if b["input"] else 0
    status = "win" if a["passed"] == b["passed"] and savings > 0 else "caution"

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jevex · routing benchmark</title>
<style>
:root {{ color-scheme:dark; --bg:#09111c; --surface:#111d2c; --line:#24364a; --text:#edf5fa; --muted:#91a7ba; --mint:#75e0c7; --violet:#b4a4f5; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
* {{ box-sizing:border-box }} html {{ scroll-behavior:smooth }} body {{ margin:0; color:var(--text); background:radial-gradient(circle at 82% 0%,#183c48 0,transparent 34%),radial-gradient(circle at 0% 40%,#161b38 0,transparent 26%),var(--bg); }}
a {{ color:var(--mint); text-underline-offset:3px }} .shell {{ max-width:1120px; margin:0 auto; padding:0 28px 72px }}
.topbar {{ display:flex; align-items:center; justify-content:space-between; padding:27px 0; border-bottom:1px solid var(--line) }}
.brand {{ font-weight:900; letter-spacing:.2em; font-size:15px }} .topbar .meta {{ color:var(--muted); font-size:13px }}
.hero {{ padding:68px 0 50px; display:grid; grid-template-columns:1.25fr .75fr; gap:34px; align-items:end }}
.eyebrow {{ color:var(--mint); text-transform:uppercase; font-size:12px; font-weight:800; letter-spacing:.18em }}
h1 {{ margin:17px 0 22px; font-size:clamp(48px,6.7vw,82px); line-height:.98; letter-spacing:-.07em }}
.intro {{ font-size:18px; line-height:1.6; color:#b8c8d6; max-width:670px }}
.verdict {{ border:1px solid #315469; background:linear-gradient(145deg,#173549,#18273f); border-radius:22px; padding:26px; box-shadow:0 25px 65px #0003 }}
.verdict.win {{ border-color:#347e6d; background:linear-gradient(145deg,#173e41,#172a3a) }}
.verdict .label,.label {{ color:var(--muted); font-weight:800; letter-spacing:.14em; text-transform:uppercase; font-size:11px }}
.verdict strong {{ display:block; margin:15px 0; font-size:27px; line-height:1.1; letter-spacing:-.04em }}
.verdict p {{ margin:0; color:#a9becb; font-size:14px; line-height:1.5 }}
.cards {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:16px 0 46px }}
.card,.panel {{ background:var(--surface); border:1px solid var(--line); border-radius:19px }} .card {{ padding:22px 24px }}
.number {{ display:block; font-weight:850; font-size:clamp(28px,3.6vw,44px); letter-spacing:-.055em; margin:10px 0 3px }}
.number.good {{ color:var(--mint) }} .number.bad {{ color:#f6a7aa }} .detail {{ color:var(--muted); font-size:13px; line-height:1.45 }}
.section-head {{ display:flex; justify-content:space-between; align-items:baseline; gap:14px; margin:40px 0 16px }} h2 {{ margin:0; letter-spacing:-.04em; font-size:26px }}
.section-head span {{ color:var(--muted); font-size:13px }} .panel {{ padding:25px 28px }}
.route {{ font-size:16px; line-height:2; overflow-wrap:anywhere }} code {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.88em; color:#c6e4ed }}
.route code {{ display:inline-block; background:#223247; border:1px solid #34475c; border-radius:8px; padding:3px 8px }} .arrow {{ color:var(--mint); margin:0 8px }}
.bar-row {{ display:grid; grid-template-columns:88px 1fr 85px; gap:18px; align-items:center; margin:21px 0 }} .bar-label,.bar-value {{ font-weight:750; font-size:14px }} .bar-value {{ text-align:right; font-variant-numeric:tabular-nums }}
.track {{ height:22px; border-radius:99px; background:#26374d; overflow:hidden }} .fill {{ height:100%; border-radius:99px }} .fill.mint {{ background:linear-gradient(90deg,#299f94,#7ae4c9) }} .fill.violet {{ background:linear-gradient(90deg,#6a61b7,#b8a6f2) }}
.tablewrap {{ overflow-x:auto }} table {{ border-collapse:collapse; width:100%; font-size:14px }} th {{ color:var(--muted); text-transform:uppercase; font-size:11px; letter-spacing:.1em; text-align:left; padding:0 12px 14px; white-space:nowrap }} td {{ border-top:1px solid var(--line); padding:17px 12px; font-variant-numeric:tabular-nums; white-space:nowrap }} td.task {{ font-weight:750 }} td.pass {{ color:var(--mint); font-weight:800 }} td.fail {{ color:#f6a7aa; font-weight:800 }}
.notes {{ display:grid; grid-template-columns:repeat(2,1fr); gap:14px }} .note {{ padding:21px 24px; border:1px solid var(--line); background:#101a29; border-radius:17px }} .note strong {{ display:block; font-size:14px; margin-bottom:7px }} .note p {{ color:var(--muted); line-height:1.55; font-size:13px; margin:0 }}
footer {{ margin-top:46px; padding-top:24px; border-top:1px solid var(--line); color:var(--muted); line-height:1.8; font-size:13px }} footer a {{ margin-right:13px }}
@media(max-width:800px) {{ .hero {{ grid-template-columns:1fr; padding-top:50px }} .cards {{ grid-template-columns:1fr }} .notes {{ grid-template-columns:1fr }} .topbar .meta {{ display:none }} .bar-row {{ grid-template-columns:70px 1fr 75px; gap:9px }} table {{ min-width:680px }} }}
</style>
</head>
<body><main class="shell">
<nav class="topbar"><div class="brand">JEVEX / LAB</div><div class="meta">RUN 001 &nbsp;·&nbsp; {escape(data['created_utc'][:10])} &nbsp;·&nbsp; {escape(data['codex_version'])}</div></nav>
    <header class="hero"><div><div class="eyebrow">A real, small-scale routing test</div><h1>Does routing<br>pay for itself?</h1><p class="intro">Four Python coding turns, twice: one conversation routed by Jevex and one pinned to GPT-6.1 Sol. We checked generated functions, measured Codex's cache and time, then priced the observed tokens.</p></div>
    <aside class="verdict {status}"><div class="label">Pilot readout</div><strong>{escape(outcome)}</strong><p>One paired session is evidence, not a forecast. The quality score comes from {sum(len(t['cases']) for t in tasks)} executable cases across four focused functions.</p></aside></header>
<section class="cards" aria-label="Key metrics">
<div class="card"><div class="label">Estimated API cost change</div><span class="number {'good' if savings > 0 else 'bad'}">{savings:+.0%}</span><div class="detail">Jevex ${a['cost']:.4f} · Fixed ${b['cost']:.4f}</div></div>
<div class="card"><div class="label">Executable checks</div><span class="number">{a['passed']}/{a['checks']}</span><div class="detail">Jevex · Fixed Sol {b['passed']}/{b['checks']}</div></div>
<div class="card"><div class="label">Elapsed session time</div><span class="number">{a['seconds']:.0f}s</span><div class="detail">Jevex · Fixed Sol {b['seconds']:.0f}s</div></div></section>
    <section class="panel"><h2>The switch changed the economics</h2><p class="intro">Jev chose Luna for the first three tasks, then Sol for the parser. The first three cost ${before_switch:.4f} versus ${before_fixed:.4f} on fixed Sol. After switching, the Jevex turn cost ${(switched['cost_usd'] or 0) + switched['router_cost_usd']:.4f} versus ${fixed_last['cost_usd']:.4f}; cached input was {switched_cache:.0%} versus {fixed_last_cache:.0%}. Most of the early saving disappeared. The switch coincided with lower cache reuse, but this run cannot prove it caused the difference.</p></section>
<div class="section-head"><h2>The route</h2><span>{model_switches} model switch{'es' if model_switches != 1 else ''} · Jev input {a['jev_tokens']:,} tokens</span></div>
<section class="panel route">{route}</section>
<div class="section-head"><h2>Estimated cost</h2><span>Standard short-context API prices, including Jev</span></div>
<section class="panel" aria-label="Estimated cost comparison">{bar('Jevex',a,'mint')}{bar('Fixed Sol',b,'violet')}</section>
<div class="section-head"><h2>Every turn</h2><span>Observed cache reuse: Jevex {a_cache:.0%} · Fixed {b_cache:.0%}</span></div>
<section class="panel tablewrap"><table><thead><tr><th>Task</th><th>Strategy</th><th>Model</th><th>Checks</th><th>Cached input</th><th>Elapsed</th><th>Est. cost</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<div class="section-head"><h2>What the pilot cannot prove</h2></div>
<section class="notes">
<div class="note"><strong>Small and ordered</strong><p>One session per strategy, Jevex first. The first routed turn read {adaptive['turns'][0]['usage'].get('cached_input_tokens',0):,} cached tokens; the first fixed turn read {fixed['turns'][0]['usage'].get('cached_input_tokens',0):,}. Cache placement, service load, and task variance can change the result. Repeated runs are needed.</p></div>
<div class="note"><strong>Narrow quality signal</strong><p>The grader checks pure Python function outputs. It does not assess repository-scale engineering, maintainability, security, or whether a tool-using Codex turn succeeds.</p></div>
<div class="note"><strong>Modeled dollars</strong><p>Codex ran through an existing login. The USD estimate applies public API list rates to reported input, cached input, cache writes, and output. It is not a ChatGPT bill or quota measurement; unreported prewarm or tool charges would be missed.</p></div>
<div class="note"><strong>Cache and context</strong><p>Codex reports cumulative session usage; we subtract prior totals. Switching models can reduce cache hits, but this pilot does not isolate that cause. The router saw only each current prompt.</p></div>
</section>
<footer>Data: <a href="results.json">results.json</a> · Reproduce: <code>python3 benchmarks/benchmark.py</code><br>
Rates: <a href="https://developers.openai.com/api/docs/models/gpt-6-luna">Luna</a><a href="https://developers.openai.com/api/docs/models/gpt-6.1-sol">Sol 6.1</a><a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a><a href="https://typesafe.ai/blog/introducing-system-one-models-and-jev">Jev pricing</a>. As of 2026-10-02. Catalog startup: {data['catalog_seconds']:.2f}s (excluded from per-session wall time).</footer>
</main></body></html>'''
