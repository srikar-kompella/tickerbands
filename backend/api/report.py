"""
api/report.py — the exportable research report (print-designed HTML).

Why HTML-to-print instead of a Python PDF library: browser rendering gives
professional typography, tables, and vector charts for free; fpdf/reportlab
give hours of layout code and a worse document. The page auto-opens the print
dialog (macOS: Save as PDF is one keystroke) and carries its own button.

Everything is inlined (CSS, SVG sparkline) — the file saved from the browser
is fully self-contained.
"""
from __future__ import annotations

import datetime as dt
import html
from typing import Any

try:
    import markdown as _md

    def md(text: str) -> str:
        return _md.markdown(text or "", extensions=["tables", "sane_lists"])
except ImportError:  # dependency missing → readable fallback, never a crash
    def md(text: str) -> str:
        return f"<pre style='white-space:pre-wrap'>{html.escape(text or '')}</pre>"


def esc(v: Any) -> str:
    return html.escape(str(v)) if v is not None else "—"


def num(v: Any, suffix: str = "", digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return esc(v)


def money(v: Any) -> str:
    return f"${float(v):,.2f}" if v is not None else "—"


def _sparkline_svg(chart: list[dict], band: dict | None) -> str:
    """30-day close polyline + dashed band bounds, as inline SVG (vector —
    prints crisply at any size)."""
    if not chart or len(chart) < 2:
        return ""
    closes = [p["close"] for p in chart]
    lo = min(closes + ([band["low"]] if band else []))
    hi = max(closes + ([band["high"]] if band else []))
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    W, H = 640, 140

    def x(i: int) -> float:
        return i / (len(closes) - 1) * (W - 70)

    def y(v: float) -> float:
        return H - (v - lo) / (hi - lo) * H

    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(closes))
    band_lines = ""
    if band:
        for price, label, color in ((band["high"], "80% high", "#0e7490"),
                                    (band["low"], "80% low", "#b45309")):
            yy = y(price)
            band_lines += (
                f'<line x1="0" y1="{yy:.1f}" x2="{W-70}" y2="{yy:.1f}" '
                f'stroke="{color}" stroke-dasharray="5 4" stroke-width="1"/>'
                f'<text x="{W-66}" y="{yy+4:.1f}" font-size="10" fill="{color}">'
                f'{label} {money(price)}</text>')
    return (f'<svg viewBox="0 0 {W} {H+10}" width="100%" height="150" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="30-day price">'
            f'<polyline points="{pts}" fill="none" stroke="#0891b2" stroke-width="2"/>'
            f'{band_lines}</svg>')


def _kv_rows(pairs: list[tuple[str, str]]) -> str:
    return "".join(f"<tr><td>{esc(k)}</td><td class='v'>{v}</td></tr>"
                   for k, v in pairs)


def build_morning_html(rows: list[dict], account: dict | None) -> str:
    """The morning sheet (roadmap #8): screener + breaks + earnings-soon +
    portfolio strip, print-titled MorningSheet_YYYY-MM-DD."""
    today = dt.date.today()
    doc = f"TickerLens_MorningSheet_{today.isoformat()}"
    breaks = [r for r in rows if r.get("outside_band_yesterday")]
    soon = [r for r in rows if r.get("days_to_earnings") is not None
            and r["days_to_earnings"] <= 7]

    def trow(r: dict) -> str:
        cls = "up" if (r.get("day_pct") or 0) > 0 else "down" if (r.get("day_pct") or 0) < 0 else ""
        flag = f" ⚠ {r['yesterday_z']}σ" if r.get("outside_band_yesterday") else ""
        return (f"<tr><td><b>{esc(r['symbol'])}</b>{flag}</td>"
                f"<td class='v {cls}'>{num(r.get('day_pct'), '%')}</td>"
                f"<td class='v'>±{num(r.get('band_half_pct'))}</td>"
                f"<td class='v'>{num(r.get('implied_vs_band'), '×')}</td>"
                f"<td class='v'>{r['days_to_earnings'] if r.get('days_to_earnings') is not None else '—'}</td>"
                f"<td class='v'>{num(r.get('last_score'), '', 0)}</td></tr>")

    port_strip = ""
    if account:
        cls = "up" if account.get("day_pl", 0) > 0 else "down" if account.get("day_pl", 0) < 0 else ""
        port_strip = (f"<div class='strip'><div><b>Portfolio</b>{money(account['total_value'])}</div>"
                      f"<div><b>Today</b><span class='{cls}'>{money(account['day_pl'])} "
                      f"({num(account['day_pl_pct'], '%')})</span></div>"
                      f"<div><b>Cash</b>{money(account['cash'])}</div></div>")

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{doc}</title>
<style>
 body{{font:13px/1.5 -apple-system,'Segoe UI',sans-serif;color:#111827;max-width:820px;margin:0 auto;padding:24px}}
 h1{{font-size:20px;margin:0}} h2{{font-size:13px;color:#0891b2;text-transform:uppercase;margin:18px 0 6px}}
 .mut{{color:#6b7280;font-size:12px}} .up{{color:#059669}} .down{{color:#dc2626}}
 table{{width:100%;border-collapse:collapse;font-size:12.5px}}
 td,th{{padding:4px 6px;border-bottom:1px solid #e5e7eb;text-align:left}}
 td.v{{text-align:right;font-variant-numeric:tabular-nums}}
 th{{font-size:10px;text-transform:uppercase;color:#6b7280}}
 .strip{{display:flex;gap:26px;margin:10px 0}} .strip b{{display:block;font-size:10px;text-transform:uppercase;color:#6b7280}}
 .printbtn{{position:fixed;top:12px;right:12px;background:#0891b2;color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer}}
 @media print{{.printbtn{{display:none}} body{{padding:0}}}}
</style></head><body>
<button class="printbtn" onclick="window.print()">Print / Save as PDF</button>
<h1>Morning sheet <span class="mut">· {today.strftime('%A, %B %d, %Y')}</span></h1>
{port_strip}
{"<h2>⚠ Unusual moves yesterday (outside the 80% band)</h2><p>" + ", ".join(f"<b>{esc(r['symbol'])}</b> ({r['yesterday_z']}σ)" for r in breaks) + "</p>" if breaks else ""}
{"<h2>Earnings within a week</h2><p>" + ", ".join(f"<b>{esc(r['symbol'])}</b> ({r['days_to_earnings']}d)" for r in sorted(soon, key=lambda x: x['days_to_earnings'])) + "</p>" if soon else ""}
<h2>Watchlist ({len(rows)})</h2>
<table><thead><tr><th>Symbol</th><th>Today</th><th>Band ±%</th><th>Impl/Band</th><th>Earn (d)</th><th>Score</th></tr></thead>
<tbody>{"".join(trow(r) for r in rows)}</tbody></table>
<p class="mut" style="margin-top:16px">Research &amp; education only — no direction predictions.
Score is the last computed value per ticker. Generated by TickerLens.</p>
<script>setTimeout(() => window.print(), 700)</script>
</body></html>"""


def build_report_html(a: dict, discussions: list[dict],
                      decisions: list[dict]) -> str:
    """Compose the full report from the analysis payload + this ticker's
    discussion/decision history. Pure function — unit-testable."""
    sym = a["symbol"]
    q = a.get("quote") or {}
    band, move, score = a.get("band"), a.get("move"), a.get("setup_score")
    f = a.get("fundamentals") or {}
    o = a.get("options") or {}
    n = a.get("news_sentiment") or {}
    cn = a.get("claude_news")
    soc = a.get("social") or {}
    recs = (a.get("recommendations") or {}).get("months") or []
    earn = a.get("earnings") or {}
    pos = a.get("position") or {}
    cov = a.get("band_coverage") or {}
    beta = a.get("beta") or {}
    wk = a.get("week52")
    now = dt.datetime.now().strftime("%B %d, %Y at %H:%M")
    # Browsers use <title> as the default save-as-PDF filename: make it a
    # filename, not a sentence — ticker first, ISO date+time for uniqueness
    # and chronological sorting (TickerLens_NVDA_2026-07-13_1432.pdf).
    doc_name = f"TickerLens_{sym}_{dt.datetime.now().strftime('%Y-%m-%d_%H%M')}"
    chg = q.get("net_change_pct")
    chg_cls = "up" if (chg or 0) > 0 else "down" if (chg or 0) < 0 else ""

    # ── section fragments ─────────────────────────────────────────────────────
    score_html = ""
    if score:
        rows = "".join(
            f"<tr><td>{esc(c['component'].replace('_', ' '))}</td>"
            f"<td class='v'>{num(c['value']) if c['available'] else 'n/a'}</td>"
            f"<td class='v'>{num(c['weight_used_pct'], '%', 1) if c['available'] else '—'}</td>"
            f"<td class='v'>{'+' if c['points'] > 0 else ''}{num(c['points'], '', 1)}</td></tr>"
            for c in score["components"])
        score_html = f"""
        <div class="card">
          <h2>Setup Score</h2>
          <div class="scorebox">
            <span class="bignum {'g' if score['score'] >= 70 else 'y' if score['score'] >= 40 else 'r'}">{num(score['score'], '', 1)}</span>
            <span class="lean">{esc(score['lean'])}</span>
            <span class="mut">conviction ×{num(score['vol_factor'])} · {score['components_available']}/{score['components_total']} signals</span>
          </div>
          <table><thead><tr><th>Signal</th><th>Value</th><th>Weight</th><th>Points</th></tr></thead>
          <tbody>{rows}</tbody></table>
          <p class="disclaimer">{esc(score['disclaimer'])}</p>
        </div>"""

    fund_html = ""
    if f.get("available"):
        fund_html = f"""<div class="card"><h2>Fundamentals</h2><table><tbody>{_kv_rows([
            ("P/E (TTM)", num(f.get("pe_ttm"), "", 1)),
            ("EPS (TTM)", money(f.get("eps_ttm"))),
            ("Market cap", f"${f['market_cap_m']/1e6:.2f}T" if (f.get('market_cap_m') or 0) >= 1e6
             else f"${f['market_cap_m']/1e3:.1f}B" if (f.get('market_cap_m') or 0) >= 1e3
             else num(f.get('market_cap_m'), 'M', 0)),
            ("Revenue growth (YoY)", num(f.get("revenue_growth_ttm_pct"), "%", 1)),
            ("Gross / Op / Net margin", f"{num(f.get('gross_margin_pct'),'%',1)} / {num(f.get('operating_margin_pct'),'%',1)} / {num(f.get('net_margin_pct'),'%',1)}"),
            ("Dividend yield", num(f.get("dividend_yield_pct"), "%")),
        ])}</tbody></table></div>"""

    opt_html = ""
    if o.get("available"):
        opt_html = f"""<div class="card"><h2>Options positioning</h2><table><tbody>{_kv_rows([
            ("Put/Call (session)", num(o.get("put_call_ratio"))),
            ("ATM implied vol", num(o.get("atm_iv_pct"), "%", 1)),
            (f"Implied move ({o.get('implied_move_dte', '—')}d)", f"±{num(o.get('implied_move_pct'))}%"),
            ("vs our band", f"{num((o.get('implied_move_pct') or 0) / band['half_width_pct'], '×') if band and band.get('half_width_pct') and o.get('implied_move_pct') is not None else '—'}"),
        ])}</tbody></table></div>"""

    heads = a.get("headlines") or []
    news_html = ""
    if n.get("available") or heads or cn:
        head_lis = "".join(
            f"<li>{esc(h['headline'])} <span class='mut'>({esc(h['source'])})</span></li>"
            for h in heads[:5])
        claude_line = (f"<p><b>Claude's news check ({esc(cn['created_ts'][:10])}):</b> "
                       f"{esc(cn['news_view']).upper()} — {esc(cn.get('news_note') or '')}</p>"
                       if cn else "")
        sent_line = (f"<p>{int((n.get('bullish_pct') or 0)*100)}% bullish / "
                     f"{int((n.get('bearish_pct') or 0)*100)}% bearish over "
                     f"{n.get('articles_week', 0)} articles "
                     f"<span class='mut'>(source: {esc(n.get('method', 'finnhub'))})</span></p>"
                     if n.get("available") else "")
        news_html = f"""<div class="card"><h2>News</h2>{sent_line}{claude_line}
        <ul>{head_lis}</ul></div>"""

    ctx_html = f"""<div class="card"><h2>Context</h2><table><tbody>{_kv_rows([
        ("Beta vs SPY (60d)", num(beta.get("beta"))),
        ("Correlation to SPY", num(beta.get("correlation"))),
        ("52-week position", f"{num(wk['position_pct'], '%', 0)} of {money(wk['low'])}–{money(wk['high'])}" if wk else "—"),
        ("Realized vol (20d)", num((a.get('signals') or {}).get('rv_20d'), '%', 1)),
        ("Social (StockTwits)", f"{soc.get('bullish', 0)} bull / {soc.get('bearish', 0)} bear, polarity {num(soc.get('polarity'))}" if soc.get("available") else "—"),
        ("Analyst mix (this mo.)", f"{recs[0]['strong_buy'] + recs[0]['buy']} buy / {recs[0]['hold']} hold / {recs[0]['sell'] + recs[0]['strong_sell']} sell of {recs[0]['total']}" if recs else "—"),
        ("Next earnings", f"{esc(earn.get('next_date'))} ({earn.get('days_until')}d)" if earn.get("available") and earn.get("next_date") else "—"),
        ("Your position", f"{num(pos.get('qty'), '', 0)} sh @ {money(pos.get('avg_cost'))} · {money(pos.get('market_value'))} ({num(pos.get('gain_pct'), '%')})" if pos.get("owned") else "not held"),
    ])}</tbody></table></div>"""

    disc_html = ""
    if discussions:
        items = ""
        for d in discussions:
            tags = " ".join(f"<span class='tag'>{esc(t)}</span>" for t in (d.get("tags") or []))
            items += f"""
            <div class="discussion">
              <h3>{esc(d.get('summary') or 'Discussion')} <span class="mut">· {esc(d['created_ts'][:16].replace('T', ' '))}</span> {tags}</h3>
              <div class="md">{md(d.get('response_markdown') or '')}</div>
            </div>"""
        disc_html = f"<div class='card page-break'><h2>Claude research discussions ({len(discussions)})</h2>{items}</div>"

    dec_html = ""
    if decisions:
        rows = "".join(
            f"<tr><td>{esc(d['created_ts'][:10])}</td><td>{esc(d['decision']).upper()}</td>"
            f"<td class='v'>{money(d['decision_price'])}</td><td class='v'>{money(d['current_price'])}</td>"
            f"<td class='v'>{num(d['pct_since'], '%')}</td></tr>" for d in decisions)
        dec_html = f"""<div class="card"><h2>Recorded decisions</h2>
        <table><thead><tr><th>When</th><th>Call</th><th>Price then</th><th>Now</th><th>Since</th></tr></thead>
        <tbody>{rows}</tbody></table></div>"""

    cov_line = (f"This exact band construction held {cov.get('covered_pct')}% of "
                f"{cov.get('days'):,} backtested days"
                + (" (basket-wide figure)" if cov.get("scope") == "basket_overall" else "")
                if cov else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{doc_name}</title>
<style>
  :root {{ --accent:#0891b2; --mut:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ font:14px/1.55 -apple-system, 'Segoe UI', sans-serif; color:#111827;
         max-width:860px; margin:0 auto; padding:28px; }}
  h1 {{ margin:0; font-size:26px; }} h2 {{ font-size:15px; text-transform:uppercase;
       letter-spacing:.04em; color:var(--accent); margin:0 0 10px; }}
  h3 {{ font-size:14px; margin:14px 0 4px; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:3px solid var(--accent); padding-bottom:12px; }}
  .brand {{ color:var(--accent); font-weight:700; letter-spacing:.02em; }}
  .mut {{ color:var(--mut); font-weight:400; font-size:12px; }}
  .price {{ font-size:24px; font-weight:700; }} .up {{ color:#059669; }} .down {{ color:#dc2626; }}
  .strip {{ display:flex; gap:28px; flex-wrap:wrap; margin:14px 0 6px; }}
  .strip b {{ display:block; font-size:11px; text-transform:uppercase; color:var(--mut); }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  .card {{ border:1px solid var(--line); border-radius:10px; padding:14px 16px;
           margin:14px 0; break-inside:avoid; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  td, th {{ padding:4px 6px; border-bottom:1px solid var(--line); text-align:left; }}
  td.v {{ text-align:right; font-variant-numeric:tabular-nums; }}
  th {{ font-size:11px; text-transform:uppercase; color:var(--mut); }}
  .scorebox {{ display:flex; align-items:baseline; gap:12px; margin-bottom:8px; }}
  .bignum {{ font-size:34px; font-weight:800; }}
  .g {{ color:#059669; }} .y {{ color:#b45309; }} .r {{ color:#dc2626; }}
  .lean {{ font-weight:700; }}
  .disclaimer {{ font-size:11px; color:var(--mut); border-top:1px solid var(--line);
                 padding-top:8px; margin:10px 0 0; }}
  .tag {{ font-size:10px; border:1px solid var(--line); border-radius:6px;
          padding:1px 6px; color:var(--mut); }}
  .discussion {{ border-top:1px dashed var(--line); padding-top:8px; margin-top:10px; }}
  .md p {{ margin:6px 0; }} .md ul, .md ol {{ margin:6px 0 6px 20px; }}
  .md h2 {{ color:#111827; text-transform:none; letter-spacing:0; font-size:14px; margin:12px 0 4px; }}
  footer {{ margin-top:22px; border-top:1px solid var(--line); padding-top:10px;
            font-size:11px; color:var(--mut); }}
  .printbtn {{ position:fixed; top:14px; right:14px; background:var(--accent); color:#fff;
               border:none; border-radius:8px; padding:9px 16px; font-size:13px;
               cursor:pointer; box-shadow:0 2px 8px rgb(0 0 0 / .18); }}
  @media print {{
    .printbtn {{ display:none; }}
    body {{ padding:0; max-width:none; }}
    .page-break {{ page-break-before:always; }}
    @page {{ margin:16mm; }}
  }}
</style></head><body>
<button class="printbtn" onclick="window.print()">Print / Save as PDF</button>

<div class="head">
  <div>
    <div class="brand">TICKERLENS · RESEARCH SNAPSHOT</div>
    <h1>{esc(sym)} <span class="mut">{esc(q.get('name') or '')}</span></h1>
    <div class="mut">Generated {esc(now)} · snapshot {esc(a.get('context_hash'))}</div>
  </div>
  <div style="text-align:right">
    <div class="price">{money(q.get('last'))}</div>
    <div class="{chg_cls}">{'+' if (chg or 0) > 0 else ''}{num(chg, '%')} today</div>
  </div>
</div>

<div class="strip">
  <div><b>80% expected range (next session)</b>
    {money(band['low']) + ' – ' + money(band['high']) + f" (±{num(band['half_width_pct'])}%)" if band else '—'}</div>
  <div><b>Today's move</b> {f"{num(move['z'])}σ" + (' · outside band' if move['outside_band'] else '') if move else '—'}</div>
  <div><b>Earnings</b> {f"in {earn['days_until']}d" if earn.get('available') and earn.get('days_until') is not None else '—'}</div>
  {f"<div><b>Claude stance ({esc((a.get('claude_stance') or {}).get('created_ts', '')[:10])})</b>{esc((a.get('claude_stance') or {}).get('stance', '')).upper()} · {esc((a.get('claude_stance') or {}).get('stance_horizon') or '')} — {esc((a.get('claude_stance') or {}).get('stance_note') or '')}</div>" if a.get('claude_stance') else ''}
</div>
{f'<p class="mut" style="margin:2px 0 8px">{esc(cov_line)}</p>' if cov_line else ''}
{_sparkline_svg(a.get('chart') or [], band)}

{score_html}
<div class="grid">
{fund_html}{opt_html}{news_html}{ctx_html}
</div>
{dec_html}
{disc_html}

<footer>
  TickerLens research &amp; education report — not investment advice. This tool
  does not predict price direction: across ~9,600 backtested predictions,
  1-day direction from these signals performed at ~coin-flip. The expected
  range is the only calibrated quantity shown{f" ({esc(cov_line)})" if cov_line else ""}.
  Data: Schwab, Finnhub, StockTwits · Generated by TickerLens.
</footer>
<script>setTimeout(() => window.print(), 700)</script>
</body></html>"""
