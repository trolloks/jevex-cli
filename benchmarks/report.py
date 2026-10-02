"""Render the benchmark's self-contained HTML report."""

from html import escape


def scenario_cost(turn, rates, cache_share, write_share=0):
    """Hold token counts constant; vary cache reads and writes, not model behavior."""
    usage = turn["usage"]
    input_rate = cache_share * rates["cached"] + (1 - cache_share) * (
        (1 - write_share) * rates["input"] + write_share * rates["write"])
    return (usage["input_tokens"] * input_rate + usage["output_tokens"] * rates["output"]) / 1_000_000 + turn["router_cost_usd"]


def cache_analysis(data):
    adaptive, fixed = data["strategies"]
    cost = lambda turn: (turn["cost_usd"] or 0) + turn["router_cost_usd"]
    routed_cost = sum(map(cost, adaptive["turns"]))
    fixed_cost = sum(map(cost, fixed["turns"]))
    routed, baseline = adaptive["turns"][-1], fixed["turns"][-1]
    usage, base_usage = routed["usage"], baseline["usage"]
    rates = data["pricing"][routed["model"]]
    input_tokens = usage["input_tokens"]
    cache_share = usage["cached_input_tokens"] / input_tokens
    base_share = base_usage["cached_input_tokens"] / base_usage["input_tokens"]
    margin = fixed_cost - routed_cost
    miss_price = (rates["input"] - rates["cached"]) / 1_000_000
    prefix_cost = routed_cost - cost(routed)
    warm_fixed = sum(scenario_cost(t, data["pricing"][t["model"]], .9) for t in fixed["turns"])
    # Accounting decomposition, not a causal estimate of the model switch.
    breakdown = [
        ("Lower cache share", input_tokens * (base_share - cache_share) * miss_price),
        ("More input tokens", (input_tokens - base_usage["input_tokens"]) * ((1 - base_share) * rates["input"] + base_share * rates["cached"]) / 1_000_000),
        ("More output tokens", (usage["output_tokens"] - base_usage["output_tokens"]) * rates["output"] / 1_000_000),
        ("Jev request", routed["router_cost_usd"]),
    ] if routed["model"] == baseline["model"] else []
    return {
        "routed_cost": routed_cost, "fixed_cost": fixed_cost, "margin": margin,
        "prefix_cost": prefix_cost, "rates": rates, "routed": routed,
        "cache_share": cache_share, "base_share": base_share,
        "break_even_share": cache_share - margin / (input_tokens * miss_price),
        "miss_headroom": margin / miss_price,
        "early_saving": sum(map(cost, fixed["turns"][:-1])) - prefix_cost,
        "last_excess": cost(routed) - cost(baseline), "breakdown": breakdown,
        "warm_fixed": warm_fixed,
    }


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
    outcome = "Viability not established"
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
    status = "caution"
    analysis = cache_analysis(data)
    breakdown_rows = "".join(f'<tr><td>{escape(label)}</td><td>${value:.5f}</td></tr>' for label, value in analysis["breakdown"])
    scenario_rows = []
    for label, share, write_share in [
        ("No cache reads", 0, 0), ("10% cached", .1, 0),
        ("Observed cache share", switched_cache, 0), ("50% cached", .5, 0),
        ("Fixed Sol cache share", fixed_last_cache, 0),
        ("No reads; noncached input written", 0, 1),
    ]:
        last_cost = scenario_cost(switched, analysis["rates"], share, write_share)
        session_cost = analysis["prefix_cost"] + last_cost
        difference = (session_cost - b["cost"]) / b["cost"]
        scenario_rows.append(f'<tr><td>{escape(label)}</td><td>${last_cost:.4f}</td><td>${session_cost:.4f}</td>'
                             f'<td class="{"fail" if difference > 0 else "pass"}">{abs(difference):.1%} {"more" if difference > 0 else "less"}</td></tr>')
    context_rows = []
    for tokens in (10_000, 30_000, 100_000):
        costs = []
        for model in ("gpt-6-luna", data["baseline"]):
            rates = data["pricing"][model]
            costs.append(f'<td>${tokens * (rates["input"] - rates["cached"]) / 1_000_000:.4f} / '
                         f'${tokens * (rates["write"] - rates["cached"]) / 1_000_000:.4f}</td>')
        context_rows.append(f'<tr><td>{tokens:,} tokens</td>{"".join(costs)}</tr>')

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
.explain {{ color:#b8c8d6; line-height:1.65; font-size:15px }} .warning {{ border-color:#78623f; background:linear-gradient(145deg,#2b251e,#151e2d) }}
.analysis-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px }} .analysis-grid .tablewrap {{ margin-top:18px; padding:0 }}
.control {{ display:flex; gap:18px; align-items:center; margin:24px 0 }} .control input {{ width:100%; accent-color:var(--mint) }} .control output {{ min-width:65px; text-align:right; font-variant-numeric:tabular-nums }}
.scenario-result {{ font-size:23px; font-weight:750; line-height:1.5 }} .formula {{ display:block; padding:15px 0; line-height:1.7; overflow-wrap:anywhere }}
footer {{ margin-top:46px; padding-top:24px; border-top:1px solid var(--line); color:var(--muted); line-height:1.8; font-size:13px }} footer a {{ margin-right:13px }}
@media(max-width:800px) {{ .hero {{ grid-template-columns:1fr; padding-top:50px }} .cards {{ grid-template-columns:1fr }} .notes,.analysis-grid {{ grid-template-columns:1fr }} .topbar .meta {{ display:none }} .bar-row {{ grid-template-columns:70px 1fr 75px; gap:9px }} table {{ min-width:680px }} }}
</style>
</head>
<body><main class="shell">
<nav class="topbar"><div class="brand">JEVEX / LAB</div><div class="meta">RUN 001 &nbsp;·&nbsp; {escape(data['created_utc'][:10])} &nbsp;·&nbsp; {escape(data['codex_version'])}</div></nav>
    <header class="hero"><div><div class="eyebrow">A real, small-scale routing test</div><h1>Does routing<br>pay for itself?</h1><p class="intro">Four Python coding turns, twice: one conversation routed by Jevex and one pinned to GPT-6.1 Sol. We checked generated functions, measured Codex's cache and time, then priced the observed tokens.</p></div>
    <aside class="verdict {status}"><div class="label">Pilot readout</div><strong>{escape(outcome)}</strong><p>The switch consumed {analysis['last_excess'] / analysis['early_saving']:.0%} of the early savings. A cold first turn on fixed Sol makes the headline especially sensitive to the comparison's starting state.</p></aside></header>
<section class="cards" aria-label="Key metrics">
<div class="card"><div class="label">Observed estimated saving</div><span class="number {'good' if savings > 0 else 'bad'}">{savings:.1%}</span><div class="detail">Jevex ${a['cost']:.4f} · Fixed ${b['cost']:.4f}</div></div>
<div class="card"><div class="label">Executable checks</div><span class="number">{a['passed']}/{a['checks']}</span><div class="detail">Jevex · Fixed Sol {b['passed']}/{b['checks']}</div></div>
<div class="card"><div class="label">Elapsed session time</div><span class="number">{a['seconds']:.0f}s</span><div class="detail">Jevex · Fixed Sol {b['seconds']:.0f}s</div></div></section>
    <section class="panel"><h2>The switch changed the economics</h2><p class="intro">Jev chose Luna for the first three tasks, then Sol for the parser. The first three cost ${before_switch:.4f} versus ${before_fixed:.4f} on fixed Sol. After switching, the Jevex turn cost ${(switched['cost_usd'] or 0) + switched['router_cost_usd']:.4f} versus ${fixed_last['cost_usd']:.4f}; cached input was {switched_cache:.0%} versus {fixed_last_cache:.0%}. Most of the early saving disappeared. The switch coincided with lower cache reuse, but this run cannot prove it caused the difference.</p></section>
<div class="section-head" id="cache-risk"><h2>Only ${analysis['margin']:.4f} of headroom</h2><span>Recorded dollars, not causal attribution</span></div>
<section class="analysis-grid">
<div class="panel warning"><h2>About {analysis['miss_headroom']:,.0f} more misses erase the win</h2><p class="explain">At the recorded Sol rates, every 1,000 tokens moved from a cache read to ordinary input adds ${(analysis['rates']['input'] - analysis['rates']['cached']) / 1000:.4f}. The last turn's cache share needs to stay above {analysis['break_even_share']:.1%} to keep this entire session cheaper, holding all other measured values fixed.</p><p class="explain">With zero cache reads on that turn, the routed session would be ${analysis['prefix_cost'] + scenario_cost(switched, analysis['rates'], 0):.4f}, or {(analysis['prefix_cost'] + scenario_cost(switched, analysis['rates'], 0) - b['cost']) / b['cost']:.1%} more than the recorded fixed session.</p></div>
<div class="panel"><h2>Where the parser's excess cost sits</h2><p class="explain">A price decomposition of the ${analysis['last_excess']:.5f} difference. Reprice routed input at fixed Sol's cache share first, then separate token-volume and output differences. This is accounting, not an isolated switch penalty.</p><div class="tablewrap"><table><thead><tr><th>Component</th><th>Extra cost</th></tr></thead><tbody>{breakdown_rows}</tbody></table></div></div>
</section>
<div class="section-head"><h2>Stress-test cache reuse</h2><span>Hypothetical · no additional model calls</span></div>
<section class="panel"><label for="cache-share">Cached share of the routed parser turn's {switched['usage']['input_tokens']:,} input tokens</label>
<div class="control"><input id="cache-share" type="range" min="0" max="100" step="any" value="{switched_cache * 100:.6f}"><output id="cache-value" for="cache-share">{switched_cache:.1%}</output></div>
<div id="scenario-result" class="scenario-result" aria-live="polite"></div><p class="explain">The first three routed turns, output tokens, router costs, and measured fixed comparison stay constant. Noncached input uses the ordinary rate. Adjusting this slider predicts cost arithmetic, not attainable cache performance or answer quality.</p>
<div class="tablewrap"><table><thead><tr><th>Parser cache assumption</th><th>Parser cost</th><th>Routed session</th><th>Versus measured fixed</th></tr></thead><tbody>{''.join(scenario_rows)}</tbody></table></div></section>
<section class="panel warning" style="margin-top:18px"><h2>The baseline's cache state can flip the result</h2><p class="explain">The fixed session started with zero cached input. If all four fixed turns instead reused 90% of their measured input tokens, with their actual output counts unchanged, its estimated cost would be ${analysis['warm_fixed']:.4f}. The recorded routed session would then be {(a['cost'] - analysis['warm_fixed']) / analysis['warm_fixed']:.0%} more expensive. This is a sensitivity scenario, not a rerun; 90% reuse is close to the fixed session's three observed follow-up turns.</p></section>
<div class="section-head"><h2>What a lost context prefix can cost</h2><span>Per miss · input only · recorded short-context rates</span></div>
<section class="panel"><p class="explain">Reprocessing the same previously cached tokens costs the difference between the destination model's cached rate and its ordinary-input or cache-write rate. The two figures below show those alternatives. They are not additive charges.</p><div class="tablewrap"><table><thead><tr><th>Previously cached tokens missed</th><th>Luna: ordinary / write</th><th>Sol: ordinary / write</th></tr></thead><tbody>{''.join(context_rows)}</tbody></table></div>
<code class="formula">extra input cost = missed cached tokens × (destination input or write rate − destination cached rate) / 1,000,000</code>
<p class="explain">This isolates cache loss within one destination model. A cross-model decision must also compare input and output rates, token counts, retries, and quality. At these rates, cold Luna input costs the same as warm Sol input ($0.10 per million); cheaper Luna output may still save money. Switching back to cold Sol can overwhelm those savings as context grows.</p>
<p class="explain">A same-model session can miss too: prefix edits, request settings, expiration, and server placement affect reuse. A model change prevents assuming that the old model's cached prefix transfers. These API mechanics are documented in <a href="https://developers.openai.com/api/docs/guides/prompt-caching">prompt caching</a> and <a href="https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics">cache diagnostics</a>; they do not specify this login's invoice.</p></section>
<div class="section-head"><h2>The route</h2><span>{model_switches} model switch{'es' if model_switches != 1 else ''} · Jev input {a['jev_tokens']:,} tokens</span></div>
<section class="panel route">{route}</section>
<div class="section-head"><h2>Estimated cost</h2><span>Standard short-context API prices, including Jev</span></div>
<section class="panel" aria-label="Estimated cost comparison">{bar('Jevex',a,'mint')}{bar('Fixed Sol',b,'violet')}</section>
<div class="section-head"><h2>Every turn</h2><span>Observed cache reuse: Jevex {a_cache:.0%} · Fixed {b_cache:.0%}</span></div>
<section class="panel tablewrap"><table><thead><tr><th>Task</th><th>Strategy</th><th>Model</th><th>Checks</th><th>Cached input</th><th>Elapsed</th><th>Est. cost</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<div class="section-head"><h2>What the pilot cannot prove</h2></div>
<section class="notes">
<div class="note"><strong>Small and selected</strong><p>One session per strategy, Jevex first. The first routed turn read {adaptive['turns'][0]['usage'].get('cached_input_tokens',0):,} cached tokens; the first fixed turn read {fixed['turns'][0]['usage'].get('cached_input_tokens',0):,}. The parser was added to exercise a switch, not sampled from a typical workload. Both fourth turns resumed in a fresh temporary directory. Cache placement, ordering, context changes, and task variance remain confounded.</p></div>
<div class="note"><strong>Narrow quality signal</strong><p>The grader checks pure Python function outputs. It does not assess repository-scale engineering, maintainability, security, or whether a tool-using Codex turn succeeds.</p></div>
<div class="note"><strong>Modeled dollars</strong><p>Codex ran through an existing login. All recorded cache-write counts are zero; this does not verify invoice-level write accounting. The USD estimate treats other input at the ordinary rate. The zero-read/write scenario explicitly prices those tokens at the write rate instead. Unreported writes, prewarming, retries, or tool charges could change the result; ChatGPT quotas are not measured here.</p></div>
<div class="note"><strong>Cache and context</strong><p>Codex reports cumulative session usage; we subtract prior totals. Switching models can reduce cache hits, but this pilot does not isolate that cause. The router saw only each current prompt.</p></div>
</section>
<div class="section-head"><h2>What would establish viability?</h2><span>The next experiment</span></div>
<section class="panel"><p class="explain">Use the same starting conversation and repository tasks for fixed Sol, unrestricted routing, and routing that switches less often. Include Sol → Luna → Sol, multiple context lengths, tools, and executable completion checks. Repeat with reversed order and measured cold and warm starts. Capture cache reads, writes, retries, full token usage, and invoice costs where available. Compare total cost per successful task rather than the cheapest isolated turn.</p><p class="explain">Until that experiment succeeds, this wrapper is a routing prototype. This pilot shows how easily cache loss can consume a nominal saving; it does not establish a reliable cost advantage.</p></section>
<footer>Data: <a href="results.json">results.json</a> · Reproduce: <code>python3 benchmarks/benchmark.py</code><br>
Rates: <a href="https://developers.openai.com/api/docs/models/gpt-6-luna">Luna</a><a href="https://developers.openai.com/api/docs/models/gpt-6.1-sol">Sol 6.1</a><a href="https://developers.openai.com/api/docs/pricing">OpenAI pricing</a><a href="https://typesafe.ai/blog/introducing-system-one-models-and-jev">Jev pricing</a>. As of 2026-10-02. Catalog startup: {data['catalog_seconds']:.2f}s (excluded from per-session wall time).</footer>
</main><script>
const slider = document.getElementById('cache-share');
function updateScenario() {{
  const share = Number(slider.value) / 100;
  const last = ({switched['usage']['input_tokens']} * ((1-share) * {analysis['rates']['input']} + share * {analysis['rates']['cached']}) + {switched['usage']['output_tokens']} * {analysis['rates']['output']}) / 1000000 + {switched['router_cost_usd']};
  const total = {analysis['prefix_cost']} + last;
  const difference = (total - {b['cost']}) / {b['cost']};
  document.getElementById('cache-value').textContent = (share * 100).toFixed(1) + '%';
  document.getElementById('scenario-result').textContent = '$' + total.toFixed(4) + ' routed total · ' + (Math.abs(difference)*100).toFixed(1) + '% ' + (difference > 0 ? 'more' : 'less') + ' than measured fixed Sol';
}}
slider.addEventListener('input', updateScenario);
updateScenario();
</script></body></html>'''
