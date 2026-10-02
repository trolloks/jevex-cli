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
    analysis = cache_analysis(data)
    a, b = analysis["routed_cost"], analysis["fixed_cost"]
    savings = (b - a) / b
    switched = adaptive["turns"][-1]
    fixed_last = fixed["turns"][-1]
    first_fixed = sum(t["cost_usd"] for t in fixed["turns"][:-1])
    last_cost = (switched["cost_usd"] or 0) + switched["router_cost_usd"]
    passed = sum(t["passed"] for t in adaptive["turns"])
    fixed_passed = sum(t["passed"] for t in fixed["turns"])
    checks = sum(t["checks"] for t in adaptive["turns"])
    check_summary = f"Both passed {passed}/{checks} checks." if passed == fixed_passed else f"Jevex passed {passed}/{checks}; Always Sol passed {fixed_passed}/{checks}."
    route = " → ".join("Luna" if t["model"] == "gpt-6-luna" else "Sol" if t["model"] == data["baseline"] else t["model"] for t in adaptive["turns"])
    rows = []
    for index, task in enumerate(tasks):
        for strategy in (adaptive, fixed):
            if index >= len(strategy["turns"]):
                continue
            turn = strategy["turns"][index]
            usage = turn["usage"]
            reuse = usage["cached_input_tokens"] / usage["input_tokens"]
            cost = (turn["cost_usd"] or 0) + turn["router_cost_usd"]
            rows.append(f'<tr><td>{escape(task["name"])}</td><td>{escape(strategy["name"])}</td>'
                        f'<td>{escape(turn["model"])}</td><td>{turn["passed"]}/{turn["checks"]}</td>'
                        f'<td>{reuse:.0%}</td><td>{cost * 100:.2f}¢</td></tr>')
    breakdown = "".join(f'<tr><td>{escape(label)}</td><td>{value * 100:.3f}¢</td></tr>' for label, value in analysis["breakdown"])
    context_rows = []
    for tokens in (10_000, 30_000, 100_000):
        costs = []
        for model in ("gpt-6-luna", data["baseline"]):
            rates = data["pricing"][model]
            costs.append(f'<td>{tokens * (rates["input"] - rates["cached"]) / 10_000:.2f}¢</td>')
        context_rows.append(f'<tr><td>{tokens:,} tokens</td>{"".join(costs)}</tr>')
    no_cache_cost = analysis["prefix_cost"] + scenario_cost(switched, analysis["rates"], 0)
    all_write_cost = analysis["prefix_cost"] + scenario_cost(switched, analysis["rates"], 0, 1)
    warm_extra = (a - analysis["warm_fixed"]) / analysis["warm_fixed"]

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jevex · does switching save money?</title>
<style>
:root {{ color-scheme:dark; --bg:#0b141e; --text:#edf5fa; --muted:#a0b5c5; --line:#2b3e4d; --mint:#79dfc6; --red:#ffb1aa; font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:radial-gradient(ellipse at 90% 0%,#173a41,transparent 40%),var(--bg); color:var(--text); }}
main {{ max-width:920px; margin:auto; padding:0 28px 64px }} a {{ color:var(--mint); text-underline-offset:3px }}
nav {{ display:flex; justify-content:space-between; padding:25px 0; border-bottom:1px solid var(--line); font-size:13px; color:var(--muted) }}
.brand {{ font-weight:800; color:var(--text); letter-spacing:.15em }} header {{ padding:46px 0 22px }}
.eyebrow {{ margin:0 0 12px; color:var(--mint); font-size:13px; font-weight:650 }}
h1 {{ max-width:780px; margin:0 0 22px; font-size:clamp(34px,5.5vw,57px); line-height:1.08; letter-spacing:-.045em }}
h2 {{ font-size:25px; letter-spacing:-.025em; margin:0 0 14px }} h3 {{ font-size:18px; margin:26px 0 10px }}
p {{ color:var(--muted); font-size:16px; line-height:1.65; margin:12px 0 }} p.lead {{ max-width:760px; font-size:20px; color:#d2e1ea }} strong {{ color:var(--text) }}
section {{ padding:28px 0; border-top:1px solid var(--line) }} .test-note {{ font-size:14px }} .route {{ color:#d2e1ea; font-size:14px }}
table {{ width:100%; border-collapse:collapse; font-size:15px; font-variant-numeric:tabular-nums }} th {{ color:var(--muted); font-weight:500; text-align:left; padding:12px 0 }} td {{ border-top:1px solid var(--line); padding:14px 0 }}
.money {{ text-align:right }} .total td {{ font-weight:750; color:var(--text) }} .tablewrap {{ overflow-x:auto }}
.whatif {{ margin:25px 0 30px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:28px 0; background:linear-gradient(90deg,#12313655,transparent) }}
label {{ display:block; margin-top:24px; font-size:16px; font-weight:650 }} .control {{ display:flex; align-items:center; gap:20px; margin:18px 0 4px }} input[type=range] {{ width:100%; accent-color:var(--mint); cursor:pointer }} output {{ min-width:64px; font-size:20px; font-variant-numeric:tabular-nums }}
.ends {{ display:flex; justify-content:space-between; font-size:12px; color:var(--muted); padding-right:84px }}
button {{ background:none; color:var(--mint); border:0; padding:0; text-decoration:underline; text-underline-offset:3px; cursor:pointer; font:inherit; font-size:13px; margin:15px 0 26px }}
.bar-row {{ display:grid; grid-template-columns:130px 1fr 86px; align-items:center; gap:15px; margin:18px 0; font-size:15px }} .track {{ height:16px; background:#243744; border-radius:3px; overflow:hidden }} .fill {{ height:100%; background:var(--mint) }} .fill.fixed {{ background:#aa9ddb }} .bar-cost {{ text-align:right; font-weight:750 }}
.result {{ font-size:23px; font-weight:750; color:var(--mint); margin-top:25px }} .result.loss {{ color:var(--red) }} .assumption {{ font-size:13px }} .caution {{ border-left:3px solid #c69d63; padding-left:18px; margin:20px 0 }}
details {{ border-top:1px solid var(--line); padding:20px 0 }} summary {{ cursor:pointer; font-size:16px; font-weight:650 }} details p,details li {{ font-size:14px; line-height:1.65; color:var(--muted) }} details table {{ font-size:13px }} details td,details th {{ padding:10px 12px 10px 0 }}
ul {{ padding-left:20px }} li {{ margin:8px 0 }} footer {{ border-top:1px solid var(--line); padding-top:22px; font-size:13px; color:var(--muted); line-height:1.8 }}
:focus-visible {{ outline:2px solid var(--mint); outline-offset:5px }}
@media(max-width:600px) {{ main {{ padding:0 20px 40px }} nav .date {{ display:none }} header {{ padding-top:32px }} .bar-row {{ grid-template-columns:104px 1fr 70px; gap:9px; font-size:13px }} .result {{ font-size:20px }} .tablewrap table {{ min-width:620px }} }}
</style>
</head>
<body><main>
<nav><span class="brand">JEVEX / LAB</span><span class="date">One small test · {escape(data['created_utc'][:10])}</span></nav>
<header>
<p class="eyebrow">Does switching models save money?</p>
<h1>One switch nearly wiped out the savings.</h1>
<p class="lead">Jevex finished {savings:.0%} cheaper, saving just {analysis['margin'] * 100:.2f}¢ across four tasks. That's too little evidence to promise reliable savings.</p>
</header>
<table aria-label="Estimated test cost in US cents">
<thead><tr><th>Estimated cost in US cents</th><th class="money">Jevex</th><th class="money">Always Sol</th></tr></thead>
<tbody>
<tr><td>First three tasks</td><td class="money">{analysis['prefix_cost'] * 100:.2f}¢</td><td class="money">{first_fixed * 100:.2f}¢</td></tr>
<tr><td>Last task, after the switch</td><td class="money">{last_cost * 100:.2f}¢</td><td class="money">{fixed_last['cost_usd'] * 100:.2f}¢</td></tr>
<tr class="total"><td>All four tasks</td><td class="money">{a * 100:.2f}¢</td><td class="money">{b * 100:.2f}¢</td></tr>
</tbody></table>
<p class="test-note">{check_summary} Jevex used <span class="route">{escape(route)}</span>. The other run used Sol for every task.</p>
<p class="test-note">We estimated costs from token counts and public API prices. These aren't actual billed charges.</p>

<section>
<h2>Why did the last task cost so much?</h2>
<p>Codex can reuse parts of the conversation it has already processed. Reused input is cheaper. This is the <strong>prompt cache</strong>.</p>
<p>On the last task, Jevex reused <strong>{analysis['cache_share']:.0%}</strong> of its input. Always Sol reused <strong>{analysis['base_share']:.0%}</strong>. Jevex also used more input and output tokens. Together, those differences ate up {analysis['last_excess'] / analysis['early_saving']:.0%} of its earlier savings.</p>
<p>We can't tell how many cache misses the switch itself caused from this one test.</p>
</section>

<div class="whatif" id="cache-risk">
<h2>What if Codex reused more or less?</h2>
<p>This slider changes how much input Codex reuses <strong>on Jevex's last task only</strong>. It shows what Jevex's <strong>total cost for all four tasks</strong> would be. The comparison run stays at {b * 100:.2f}¢.</p>
<label for="cache-share">How much of the last task's input is reused?</label>
<div class="control"><input id="cache-share" type="range" min="0" max="100" step="any" value="{analysis['cache_share'] * 100:.6f}" aria-describedby="slider-help"><output id="cache-value" for="cache-share">{analysis['cache_share']:.1%}</output></div>
<div class="ends"><span>0% · reuse nothing, pay more</span><span>100% · reuse everything, pay less</span></div>
<button id="reset-cache" type="button">Reset to the test result, {analysis['cache_share']:.1%}</button>
<div class="bar-row"><span>Jevex total</span><div class="track"><div class="fill" id="jevex-bar"></div></div><span class="bar-cost" id="jevex-cost">{a * 100:.2f}¢</span></div>
<div class="bar-row"><span>Always Sol total</span><div class="track"><div class="fill fixed" id="fixed-bar"></div></div><span class="bar-cost">{b * 100:.2f}¢</span></div>
<p id="scenario-result" class="result" aria-live="polite"></p>
<p id="slider-help" class="assumption">This is a what-if calculation, not another test. Only the last task's reused input changes. Its input length, output, model, and all other tasks stay the same.</p>
<p class="assumption">Below {analysis['break_even_share']:.1%} reuse, Jevex costs more overall. With no reuse, it would cost {no_cache_cost * 100:.2f}¢, or {(no_cache_cost - b) / b:.0%} more than Always Sol.</p>
</div>

<section>
<h2>So, is Jevex worth it?</h2>
<p><strong>We don't know yet.</strong> The saving in this test was only {analysis['margin'] * 100:.2f}¢. Reprocessing about {analysis['miss_headroom']:,.0f} more input tokens on Sol would erase it.</p>
<p class="caution">The comparison also started without any cached input. If it had reused 90% of its input on all four tasks, its estimated cost would be {analysis['warm_fixed'] * 100:.2f}¢. The recorded Jevex run would cost {warm_extra:.0%} more. That's an assumption, not a result we measured.</p>
<p>Next, we need repeated tests on real coding work, with both runs starting from the same conversation and cache conditions. We should include switches back to Sol and compare the full cost of getting the work done.</p>
</section>

<details>
<summary>See the task results and test limits</summary>
<div class="tablewrap"><table><thead><tr><th>Task</th><th>Run</th><th>Model</th><th>Checks passed</th><th>Input reused</th><th>Cost</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<ul>
<li>We ran four small Python tasks once per approach. Passing these checks doesn't prove the models can complete larger coding jobs.</li>
<li>We added the last task specifically to get a model switch. These tasks aren't a sample of everyday coding work.</li>
<li>Jevex ran first. Its first task reused some input; the other run's first task reused none. The starting cache conditions weren't equal.</li>
<li>We resumed both conversations in a new temporary directory for the last task. That changed the request context too.</li>
<li>Codex reported running token totals. We subtracted the earlier totals to get each task's usage. Jev only saw the current prompt when choosing a model.</li>
</ul>
<p>For a stronger test, use the same starting conversation, repeat in both orders, try longer contexts and tool use, and compare Always Sol with routing that switches less often. Count retries and failed work too.</p>
</details>

<details>
<summary>See how we estimated cache costs</summary>
<h3>Why the last task cost {analysis['last_excess'] * 100:.2f}¢ more</h3>
<p>This splits the price difference by cache reuse and token counts. It doesn't prove the switch caused every cache miss.</p>
<table><thead><tr><th>Difference</th><th>Extra cost</th></tr></thead><tbody>{breakdown}</tbody></table>
<h3>What happens when previously reused text needs processing again?</h3>
<p>Each row below shows the added cost of processing that many tokens at the regular input rate instead of the cheaper cached rate. It doesn't include output or retries.</p>
<table><thead><tr><th>Input processed again</th><th>Extra on Luna</th><th>Extra on Sol</th></tr></thead><tbody>{''.join(context_rows)}</tbody></table>
<p>At the rates used here, new Luna input costs the same per token as cached Sol input. Luna's cheaper output can still help. Switching back to Sol is much more expensive if it needs to process a long conversation again.</p>
<h3>What about charges for writing a new cache?</h3>
<p>The API has separate prices for reading cached input, processing new input, and saving new input into the cache. Our logs listed zero cache writes, so we priced other input at the regular rate. We haven't checked those counts against an invoice.</p>
<p>If Jevex reused none of the last task's input and all of that input was charged at the cache-write rate, its total would be {all_write_cost * 100:.2f}¢. That's {(all_write_cost - b) / b:.0%} more than the recorded comparison. This is a separate what-if example; the slider uses regular input prices.</p>
<p>A model change can prevent reuse. Changes to the input or settings, an expired cache, or server placement can also cause misses. See OpenAI's <a href="https://developers.openai.com/api/docs/guides/prompt-caching">cache guide</a> and <a href="https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics">cache diagnostics</a>.</p>
<p>Prices used for this test are from {escape(data['created_utc'][:10])}: <a href="https://developers.openai.com/api/docs/pricing">OpenAI API prices</a> and <a href="https://typesafe.ai/blog/introducing-system-one-models-and-jev">Jev prices</a>. We included Jev's routing charge. These figures don't measure ChatGPT billing or usage limits.</p>
</details>

<footer><a href="results.json">Download the raw results</a> · {escape(data['codex_version'])}<br>Repeat the test with <code>python3 benchmarks/benchmark.py</code>. It runs eight real Codex tasks and spends tokens.</footer>
</main>
<script>
const slider = document.getElementById('cache-share');
const observed = {analysis['cache_share'] * 100};
const fixedCost = {b};
function updateScenario() {{
  const share = Number(slider.value) / 100;
  const last = ({switched['usage']['input_tokens']} * ((1-share) * {analysis['rates']['input']} + share * {analysis['rates']['cached']}) + {switched['usage']['output_tokens']} * {analysis['rates']['output']}) / 1000000 + {switched['router_cost_usd']};
  const total = {analysis['prefix_cost']} + last;
  const gap = fixedCost - total;
  const maxCost = Math.max(total, fixedCost);
  document.getElementById('cache-value').textContent = (share * 100).toFixed(1) + '%';
  document.getElementById('jevex-cost').textContent = (total * 100).toFixed(2) + '¢';
  document.getElementById('jevex-bar').style.width = (total / maxCost * 100) + '%';
  document.getElementById('fixed-bar').style.width = (fixedCost / maxCost * 100) + '%';
  const result = document.getElementById('scenario-result');
  result.classList.toggle('loss', gap < 0);
  result.textContent = Math.abs(gap) < 0.000005 ? 'Both runs cost about the same.' :
    'Jevex would cost ' + (Math.abs(gap) * 100).toFixed(2) + '¢ ' + (gap >= 0 ? 'less' : 'more') + ' overall, ' + (Math.abs(gap) / fixedCost * 100).toFixed(1) + '% ' + (gap >= 0 ? 'cheaper.' : 'more expensive.');
}}
slider.addEventListener('input', updateScenario);
document.getElementById('reset-cache').addEventListener('click', () => {{
  slider.value = observed;
  updateScenario();
}});
updateScenario();
</script>
</body></html>'''
