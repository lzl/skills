"""Self-contained HTML renderer for the Bitcoin market-regime dashboard."""

from __future__ import annotations

import html
import json
from typing import Any


DISCLAIMER = "This is not financial advice. It is a rules-based market-regime dashboard."


def money(value: Any) -> str:
    try:
        if value is None:
            return "Unavailable"
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "Unavailable"


def number(value: Any, digits: int = 1) -> str:
    try:
        if value is None:
            return "Unavailable"
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "Unavailable"


def pct(value: Any) -> str:
    try:
        if value is None:
            return "Unavailable"
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "Unavailable"


def pill(label: str, state: str = "neutral") -> str:
    return f'<span class="pill pill-{html.escape(state)}">{html.escape(label)}</span>'


def list_items(items: list[str], empty: str) -> str:
    if not items:
        return f"<li>{html.escape(empty)}</li>"
    return "\n".join(f"<li>{html.escape(str(item))}</li>" for item in items)


def metric(label: str, value: str, detail: str = "") -> str:
    return (
        '<div class="metric">'
        f'<span class="metric-label">{html.escape(label)}</span>'
        f'<strong>{html.escape(value)}</strong>'
        f'<small>{html.escape(detail)}</small>'
        "</div>"
    )


def status_word(value: bool | None) -> str:
    if value is True:
        return "Active"
    if value is False:
        return "Inactive"
    return "Unavailable"


def render_dashboard(report: dict[str, Any]) -> str:
    btc = report.get("btc", {})
    scores = report.get("scores", {})
    classification = report.get("classification", {})
    indicators = report.get("indicators", {})
    daily = indicators.get("daily", {})
    weekly = indicators.get("weekly", {})
    monthly = indicators.get("monthly", {})
    two_month = indicators.get("two_month", {})
    three_month = indicators.get("three_month", {})
    onchain = indicators.get("onchain", {})
    data_sources = report.get("data_sources", {})
    chart_data = daily.get("price_history_365", [])
    phase = classification.get("phase", "Data Unavailable")
    confidence = scores.get("confidence", "Low")
    confidence_state = {"High": "positive", "Medium": "watch", "Low": "negative"}.get(confidence, "neutral")
    cache_banner = ""
    if data_sources.get("cache_used"):
        cache_banner = '<div class="banner warning">Live data failed. This dashboard is using cached market data.</div>'
    elif data_sources.get("fallback_used"):
        cache_banner = '<div class="banner watch">Primary market data failed. A public fallback source was used.</div>'
    if report.get("error"):
        cache_banner = '<div class="banner danger">Market data is unavailable. The dashboard rendered an error state instead of crashing.</div>'

    bottom_score = scores.get("bottom_zone_score", 0)
    bottom_max = scores.get("bottom_zone_score_max", 0) or 1
    trend_score = scores.get("trend_confirmation_score", 0)
    trend_max = scores.get("trend_confirmation_score_max", 0) or 1
    bottom_width = min(100, max(0, float(bottom_score) / float(bottom_max) * 100))
    trend_width = min(100, max(0, float(trend_score) / float(trend_max) * 100))

    chart_json = json.dumps(chart_data, separators=(",", ":"))
    source_errors = data_sources.get("errors", [])
    unavailable_onchain = [
        name.replace("_", " ").title()
        for name, payload in onchain.items()
        if isinstance(payload, dict) and not payload.get("available")
    ]

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>When Buy Bitcoin?</title>
  <style>
    :root {{
      --ink: #1f2522;
      --muted: #66716b;
      --soft: #f4f5f0;
      --paper: #fbfbf7;
      --panel: #ffffff;
      --line: #dfe3d7;
      --accent: #c27a2c;
      --blue: #315f7d;
      --green: #49715d;
      --red: #9d463d;
      --gold: #b58a32;
      --shadow: 0 18px 50px rgba(31, 37, 34, 0.08);
      --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(49, 95, 125, 0.08) 0 1px, transparent 1px 100%),
        linear-gradient(180deg, #f8f8f2 0%, #eef1eb 100%);
      background-size: 48px 48px, auto;
      font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
      line-height: 1.5;
    }}
    .shell {{
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 34px 0 44px;
    }}
    header {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(280px, 0.8fr);
      gap: clamp(20px, 5vw, 64px);
      align-items: end;
      padding: clamp(18px, 3vw, 30px) 0 clamp(20px, 4vw, 42px);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino", Georgia, serif;
      font-size: clamp(3rem, 7vw, 7.2rem);
      line-height: 0.88;
      letter-spacing: 0;
      font-weight: 600;
    }}
    .subtitle {{
      margin: 18px 0 0;
      max-width: 620px;
      color: var(--muted);
      font-size: clamp(1rem, 1.2vw, 1.2rem);
    }}
    .stamp {{
      justify-self: end;
      width: min(100%, 420px);
      border-left: 4px solid var(--accent);
      padding: 0 0 0 18px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .stamp strong {{ display: block; color: var(--ink); font-size: 1.05rem; }}
    .banner {{
      margin: 18px 0 0;
      padding: 12px 14px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      font-weight: 650;
    }}
    .banner.warning {{ border-color: color-mix(in srgb, var(--gold), var(--line)); color: #694d18; }}
    .banner.watch {{ border-color: color-mix(in srgb, var(--blue), var(--line)); color: var(--blue); }}
    .banner.danger {{ border-color: color-mix(in srgb, var(--red), var(--line)); color: var(--red); }}
    .overview {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 18px;
      margin-top: 22px;
    }}
    .phase-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: clamp(18px, 3vw, 28px);
    }}
    .eyebrow {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.76rem;
      font-weight: 800;
    }}
    .phase-panel h2 {{
      margin: 12px 0 10px;
      font-size: clamp(1.8rem, 4vw, 3.6rem);
      line-height: 1;
      letter-spacing: 0;
      max-width: 860px;
    }}
    .action {{
      max-width: 780px;
      margin: 0;
      color: var(--ink);
      font-size: clamp(1.05rem, 1.4vw, 1.25rem);
      font-weight: 650;
    }}
    .score-panel {{
      display: grid;
      gap: 12px;
      align-content: stretch;
    }}
    .score-card {{
      background: var(--ink);
      color: #f7f5eb;
      border-radius: 8px;
      padding: 18px;
      min-height: 0;
    }}
    .score-card.alt {{ background: var(--blue); }}
    .score-card strong {{
      display: block;
      font-size: clamp(2rem, 5vw, 3.4rem);
      line-height: 1;
      margin: 4px 0 10px;
    }}
    .track {{
      height: 7px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.24);
    }}
    .bar {{
      height: 100%;
      width: var(--w);
      border-radius: inherit;
      background: #f3d39a;
      transition: width 220ms var(--ease-out);
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 16px 0 26px;
    }}
    .metric {{
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 112px;
    }}
    .metric-label {{
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .metric strong {{
      display: block;
      margin-top: 10px;
      font-size: clamp(1.3rem, 2vw, 1.85rem);
      line-height: 1;
      overflow-wrap: anywhere;
    }}
    .metric small {{ display: block; color: var(--muted); margin-top: 10px; }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(300px, 0.42fr);
      gap: clamp(18px, 3vw, 28px);
      align-items: start;
    }}
    .section {{
      padding: 24px 0;
      border-top: 1px solid var(--line);
    }}
    .section h3 {{
      margin: 0 0 14px;
      font-size: 1.15rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .section p {{ margin: 0; color: var(--muted); }}
    .columns {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    ul.clean {{
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }}
    ul.clean li {{
      position: relative;
      padding-left: 18px;
    }}
    ul.clean li::before {{
      content: "";
      position: absolute;
      left: 0;
      top: 0.72em;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent);
    }}
    .chart-wrap {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: var(--shadow);
    }}
    canvas {{
      display: block;
      width: 100%;
      height: 300px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 3px 9px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.65);
      font-size: 0.82rem;
      font-weight: 750;
      white-space: nowrap;
    }}
    .pill-positive {{ color: var(--green); border-color: color-mix(in srgb, var(--green), var(--line)); }}
    .pill-watch {{ color: #76561c; border-color: color-mix(in srgb, var(--gold), var(--line)); }}
    .pill-negative {{ color: var(--red); border-color: color-mix(in srgb, var(--red), var(--line)); }}
    .detail-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }}
    .detail-table th, .detail-table td {{
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    .detail-table th {{ color: var(--muted); font-weight: 750; width: 46%; }}
    footer {{
      margin-top: 26px;
      padding: 18px 0 0;
      border-top: 2px solid var(--ink);
      font-weight: 800;
    }}
    @media (hover: hover) and (pointer: fine) {{
      .metric {{
        transition: transform 160ms var(--ease-out), border-color 160ms var(--ease-out);
      }}
      .metric:hover {{
        transform: translateY(-2px);
        border-color: color-mix(in srgb, var(--accent), var(--line));
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .metric, .bar {{ transition: none; }}
    }}
    @media (max-width: 860px) {{
      header, .overview, .grid, .columns {{ grid-template-columns: 1fr; }}
      .stamp {{ justify-self: start; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      canvas {{ height: 250px; }}
    }}
    @media (max-width: 560px) {{
      .shell {{ width: min(100vw - 20px, 1180px); padding-top: 18px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .phase-panel, .score-card, .metric, .chart-wrap {{ border-radius: 7px; }}
      h1 {{ font-size: clamp(2.7rem, 18vw, 4.4rem); }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <div class="eyebrow">BTC market regime</div>
        <h1>When Buy Bitcoin?</h1>
        <p class="subtitle">Daily rules-based BTC market-regime analysis</p>
      </div>
      <div class="stamp">
        <strong>Generated {html.escape(str(report.get("generated_at_utc", "Unavailable")))} UTC</strong>
        Data freshness: {html.escape(str(report.get("data_freshness", "Unavailable")))}<br>
        Market source: {html.escape(str(data_sources.get("market", "Unavailable")))}
      </div>
    </header>
    {cache_banner}

    <section class="overview">
      <div class="phase-panel">
        <div class="eyebrow">Current phase {pill(str(confidence), confidence_state)}</div>
        <h2>{html.escape(str(phase))}</h2>
        <p class="action">{html.escape(str(classification.get("recommended_action", "No action.")))}</p>
      </div>
      <aside class="score-panel" aria-label="Scores">
        <div class="score-card">
          <span>Bottom Zone Score</span>
          <strong>{html.escape(str(bottom_score))}/{html.escape(str(bottom_max))}</strong>
          <div class="track"><div class="bar" style="--w: {bottom_width:.1f}%"></div></div>
        </div>
        <div class="score-card alt">
          <span>Trend Confirmation Score</span>
          <strong>{html.escape(str(trend_score))}/{html.escape(str(trend_max))}</strong>
          <div class="track"><div class="bar" style="--w: {trend_width:.1f}%"></div></div>
        </div>
      </aside>
    </section>

    <section class="metrics" aria-label="Key metrics">
      {metric("Current BTC price", money(btc.get("current_price")), "latest closed daily close")}
      {metric("Latest daily candle", str(btc.get("latest_closed_daily") or "Unavailable"), "UTC close logic")}
      {metric("Latest weekly candle", str(btc.get("latest_closed_weekly") or "Unavailable"), "week ending Sunday")}
      {metric("Confidence", str(confidence), "availability + confluence")}
    </section>

    <section class="grid">
      <div>
        <div class="section">
          <h3>Main reasons</h3>
          <ul class="clean">{list_items(classification.get("main_reasons", []), "No active confluence reasons were detected.")}</ul>
        </div>
        <div class="section">
          <h3>Missing confirmations</h3>
          <ul class="clean">{list_items(classification.get("missing_confirmations", []), "No major missing confirmations were listed.")}</ul>
        </div>
        <div class="section">
          <h3>Next levels to watch</h3>
          <ul class="clean">{list_items(classification.get("next_levels", []), "No watch levels were available.")}</ul>
        </div>
      </div>
      <div class="chart-wrap">
        <div class="eyebrow">Price structure</div>
        <canvas id="priceChart" width="720" height="360" aria-label="BTC price chart"></canvas>
      </div>
    </section>

    <section class="section">
      <h3>Price Structure</h3>
      <div class="columns">
        <table class="detail-table">
          <tr><th>Daily 50D SMA</th><td>{money(daily.get("sma_50"))} ({pct(daily.get("sma_50_distance_pct"))})</td></tr>
          <tr><th>Daily 200D SMA</th><td>{money(daily.get("sma_200"))} ({pct(daily.get("sma_200_distance_pct"))})</td></tr>
          <tr><th>50D / 200D golden cross</th><td>{status_word(daily.get("golden_cross_50_200"))}</td></tr>
          <tr><th>50D / 200D death cross</th><td>{status_word(daily.get("death_cross_50_200"))}</td></tr>
        </table>
        <table class="detail-table">
          <tr><th>BMSB range</th><td>{money(weekly.get("bmsb_lower"))} - {money(weekly.get("bmsb_upper"))}</td></tr>
          <tr><th>Price above BMSB</th><td>{status_word(weekly.get("price_above_bmsb"))}</td></tr>
          <tr><th>Weekly close above 50W SMA</th><td>{status_word(weekly.get("price_above_50w_sma"))}</td></tr>
          <tr><th>Distance to 200W SMA</th><td>{pct(weekly.get("distance_to_200w_pct"))}</td></tr>
        </table>
      </div>
    </section>

    <section class="section">
      <h3>On-Chain Valuation</h3>
      <div class="columns">
        <table class="detail-table">
          <tr><th>MVRV Z-Score</th><td>{number(onchain.get("mvrv_z_score", {}).get("value"), 2)}</td></tr>
          <tr><th>MVRV Ratio</th><td>{number(onchain.get("mvrv_ratio", {}).get("value"), 2)}</td></tr>
          <tr><th>Realized Price</th><td>{money(onchain.get("realized_price", {}).get("value"))}</td></tr>
          <tr><th>Balanced Price</th><td>{money(onchain.get("balanced_price", {}).get("value"))}</td></tr>
        </table>
        <table class="detail-table">
          <tr><th>Supply in Profit</th><td>{number(onchain.get("supply_profit", {}).get("value"), 0)}</td></tr>
          <tr><th>Supply in Loss</th><td>{number(onchain.get("supply_loss", {}).get("value"), 0)}</td></tr>
          <tr><th>Unavailable metrics</th><td>{html.escape(", ".join(unavailable_onchain[:6]) or "None")}</td></tr>
          <tr><th>On-chain source</th><td>{html.escape(str(data_sources.get("onchain", "Unavailable")))}</td></tr>
        </table>
      </div>
    </section>

    <section class="section">
      <h3>Momentum Exhaustion</h3>
      <div class="columns">
        <table class="detail-table">
          <tr><th>Daily RSI 14</th><td>{number(daily.get("rsi_14"), 1)}</td></tr>
          <tr><th>Daily Stoch RSI K / D</th><td>{number(daily.get("stoch_rsi_k"), 1)} / {number(daily.get("stoch_rsi_d"), 1)}</td></tr>
          <tr><th>Daily LMACD histogram</th><td>{number(daily.get("lmacd_histogram"), 4)}</td></tr>
          <tr><th>Simplified BBWP</th><td>{number(daily.get("simplified_bbwp"), 1)}</td></tr>
        </table>
        <table class="detail-table">
          <tr><th>Monthly Stoch RSI below 20</th><td>{status_word(monthly.get("stoch_rsi_below_20"))}</td></tr>
          <tr><th>2M Stoch RSI near zero</th><td>{status_word(two_month.get("stoch_rsi_near_zero"))}</td></tr>
          <tr><th>3M LMACD red histogram</th><td>{status_word(three_month.get("lmacd_histogram_red"))}</td></tr>
          <tr><th>3M bearish momentum deepening</th><td>{status_word(three_month.get("bearish_momentum_deepening"))}</td></tr>
        </table>
      </div>
    </section>

    <section class="section">
      <h3>Right-Side Confirmation</h3>
      <div class="columns">
        <table class="detail-table">
          <tr><th>Weekly BMSB reclaim</th><td>{status_word(weekly.get("bmsb_reclaim"))}</td></tr>
          <tr><th>Reclaim with volume</th><td>{status_word(weekly.get("bmsb_reclaim_with_volume"))}</td></tr>
          <tr><th>BMSB / 50W bullish structure</th><td>{status_word(weekly.get("bmsb_50w_bullish_cross_or_reclaim_structure"))}</td></tr>
        </table>
        <table class="detail-table">
          <tr><th>Price below 100W SMA</th><td>{status_word(weekly.get("price_below_100w_sma"))}</td></tr>
          <tr><th>Two closes below 100W SMA</th><td>{status_word(weekly.get("two_consecutive_weekly_closes_below_100w_sma"))}</td></tr>
          <tr><th>BMSB / 50W bearish structure</th><td>{status_word(weekly.get("bmsb_50w_death_cross_or_bearish_structure"))}</td></tr>
        </table>
      </div>
    </section>

    <section class="section">
      <h3>Data Quality</h3>
      <div class="columns">
        <table class="detail-table">
          <tr><th>Market data</th><td>{html.escape(str(data_sources.get("market", "Unavailable")))}</td></tr>
          <tr><th>Fallback used</th><td>{status_word(data_sources.get("fallback_used"))}</td></tr>
          <tr><th>Cache used</th><td>{status_word(data_sources.get("cache_used"))}</td></tr>
          <tr><th>Closed-candle policy</th><td>Confirmed signals use closed UTC candles only.</td></tr>
        </table>
        <div>
          <p>{html.escape(str(classification.get("summary", "")))}</p>
          <ul class="clean" style="margin-top: 12px;">{list_items(source_errors, "No data-source errors were reported.")}</ul>
        </div>
      </div>
    </section>

    <footer>{html.escape(DISCLAIMER)}</footer>
  </main>

  <script type="application/json" id="chart-data">{chart_json}</script>
  <script>
    (function() {{
      const raw = document.getElementById('chart-data').textContent;
      const data = JSON.parse(raw).filter(d => d && Number.isFinite(d.close));
      const canvas = document.getElementById('priceChart');
      const ctx = canvas.getContext('2d');
      function draw() {{
        const ratio = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = Math.max(320, Math.floor(rect.width * ratio));
        canvas.height = Math.max(220, Math.floor(rect.height * ratio));
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        const w = rect.width;
        const h = rect.height;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        if (data.length < 2) {{
          ctx.fillStyle = '#66716b';
          ctx.font = '14px Avenir Next, sans-serif';
          ctx.fillText('Price history unavailable', 18, 32);
          return;
        }}
        const pad = {{ left: 56, right: 18, top: 18, bottom: 34 }};
        const values = data.flatMap(d => [d.close, d.sma_50, d.sma_200]).filter(Number.isFinite);
        const min = Math.min(...values) * 0.96;
        const max = Math.max(...values) * 1.04;
        const x = i => pad.left + (i / (data.length - 1)) * (w - pad.left - pad.right);
        const y = v => pad.top + (1 - ((v - min) / (max - min))) * (h - pad.top - pad.bottom);

        ctx.strokeStyle = '#dfe3d7';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i < 4; i += 1) {{
          const yy = pad.top + i * (h - pad.top - pad.bottom) / 3;
          ctx.moveTo(pad.left, yy);
          ctx.lineTo(w - pad.right, yy);
        }}
        ctx.stroke();

        function line(key, color, width) {{
          ctx.strokeStyle = color;
          ctx.lineWidth = width;
          ctx.beginPath();
          let started = false;
          data.forEach((d, i) => {{
            const value = d[key];
            if (!Number.isFinite(value)) return;
            if (!started) {{
              ctx.moveTo(x(i), y(value));
              started = true;
            }} else {{
              ctx.lineTo(x(i), y(value));
            }}
          }});
          ctx.stroke();
        }}
        line('sma_200', '#9d463d', 1.4);
        line('sma_50', '#315f7d', 1.4);
        line('close', '#1f2522', 2.6);

        ctx.fillStyle = '#66716b';
        ctx.font = '12px Avenir Next, sans-serif';
        ctx.fillText('$' + Math.round(max).toLocaleString(), 8, pad.top + 4);
        ctx.fillText('$' + Math.round(min).toLocaleString(), 8, h - pad.bottom);
        ctx.fillStyle = '#1f2522';
        ctx.fillText('Close', pad.left, h - 10);
        ctx.fillStyle = '#315f7d';
        ctx.fillText('50D', pad.left + 56, h - 10);
        ctx.fillStyle = '#9d463d';
        ctx.fillText('200D', pad.left + 100, h - 10);
      }}
      draw();
      window.addEventListener('resize', draw, {{ passive: true }});
    }})();
  </script>
</body>
</html>
"""
    return html_doc
