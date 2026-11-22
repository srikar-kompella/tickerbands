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
import re
from typing import Any

from analysis import news_relevance, research_quality

_LIST_ITEM = re.compile(r"^( *)([-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")


def normalize_md(text: str) -> str:
    """Make research notes render the way their authors (and the app's own
    CommonMark view) see them. Python-Markdown needs a blank line before a
    list that follows a paragraph, and 4 spaces per nesting level; sessions
    write CommonMark — a list straight under a sentence, nested by 2 spaces.
    NVDA 11/22: "- Q4 FY26: 34.9 … - Q1 + Q2 …" ran together in the export.
    Code fences and tables are left alone."""
    out: list[str] = []
    in_fence = False
    stack: list[int] = []          # source indents of the open list levels
    prev = ""
    for line in (text or "").split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            prev, stack = line, []
            continue
        m = None if in_fence else _LIST_ITEM.match(line)
        if m:
            ind = len(m.group(1))
            prev_is_item = bool(_LIST_ITEM.match(prev)) or (prev.startswith(" ") and stack)
            if not stack or not prev.strip():
                stack = [ind]
            else:
                while stack and ind < stack[-1]:
                    stack.pop()
                if not stack or ind > stack[-1]:
                    stack.append(ind)
            if prev.strip() and not prev_is_item and not prev.lstrip().startswith("|"):
                out.append("")             # a list straight under a paragraph
            level = len(stack) - 1
            line = " " * (4 * level) + line[ind:]
        elif not in_fence and not line.strip():
            stack = []
        elif not in_fence and stack and line.startswith(" "):
            # continuation of a list item: re-indent to the item's level
            line = " " * (4 * len(stack)) + line.lstrip()
        else:
            stack = [] if not in_fence and not line.startswith(" ") else stack
        out.append(line)
        prev = line
    return "\n".join(out)


try:
    import markdown as _md

    def md(text: str) -> str:
        return _md.markdown(normalize_md(text or ""), extensions=["tables", "sane_lists"])
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
    doc = f"TickerBands_MorningSheet_{today.isoformat()}"
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
Score is the last computed value per ticker. Generated by TickerBands.</p>
<script>setTimeout(() => window.print(), 700)</script>
</body></html>"""


# row order of the two variants of the first card (keys absent from the
# report view are skipped)
FUNDAMENTAL_ROWS = [
    "bank_note", "market_cap", "pe_ttm", "eps_ttm", "p_tbv", "roe", "p_fcf", "fcf_ttm",
    "ev", "net_cash", "revenue_growth", "revenue_growth_q", "gross_margin",
    "operating_margin", "net_margin", "net_margin_q", "div_yield_forward",
    "div_yield_trailing"]
FUND_ROWS = [
    "div_yield_trailing", "div_yield_forward", "fund_schedule", "fund_distributions",
    "fund_expense_ratio", "fund_aum", "fund_nav", "fund_category"]


def _chips(f: dict | None) -> str:
    if not f:
        return ""
    return "".join(f"<span class='chip'>⚠ {esc(w)}</span>" for w in (f.get("warnings") or []))


def _frow(f: dict | None) -> str:
    """One field row: label · basis note | value | warning chips. Withheld or
    missing values render '—' — the chip says why."""
    if not f:
        return ""
    basis = f" <span class='basis'>{esc(f['basis'])}</span>" if f.get("basis") else ""
    return (f"<tr><td>{esc(f['label'])}{basis}</td>"
            f"<td class='v'>{esc(f['display'])}</td></tr>"
            + (f"<tr class='chiprow'><td colspan='2'>{_chips(f)}</td></tr>"
               if f.get("warnings") else ""))


def _fcard(title: str, fields: dict, keys: list[str], extra: str = "") -> str:
    rows = "".join(_frow(fields.get(k)) for k in keys if fields.get(k))
    if not rows and not extra:
        return ""
    return f"<div class='card'><h2>{esc(title)}</h2><table><tbody>{rows}</tbody></table>{extra}</div>"


def build_report_html(a: dict, discussions: list[dict],
                      decisions: list[dict]) -> str:
    """Compose the full report from the analysis payload + this ticker's
    discussion/decision history. Pure function — unit-testable.

    Every number comes from a["report_view"] (analysis/report_view.py): one
    price basis, labeled sources, consistency-checked. Nothing here computes
    or reformats a number on its own."""
    sym = a["symbol"]
    q = a.get("quote") or {}
    rv = a.get("report_view") or {"fields": {}, "checks": [], "data_as_of": {}}
    F = rv.get("fields") or {}
    asof = rv.get("data_as_of") or {}
    basis = a.get("price_basis") or {}
    score = a.get("setup_score")
    cov = a.get("band_coverage") or {}
    now = dt.datetime.now().strftime("%B %d, %Y at %H:%M")
    doc_name = f"TickerBands_{sym}_{dt.datetime.now().strftime('%Y-%m-%d_%H%M')}"
    dm = (F.get("day_move") or {})
    chg = dm.get("value") if dm.get("status") == "ok" else None
    chg_cls = "up" if (chg or 0) > 0 else "down" if (chg or 0) < 0 else ""
    failed = [c for c in (rv.get("checks") or []) if not c["ok"]]

    def disp(k: str) -> str:
        return esc((F.get(k) or {}).get("display", "—"))

    # ── data-as-of + failed checks ───────────────────────────────────────────
    asof_html = f"""<div class="asof"><b>Data as of</b>
      <div>Price: {esc(asof.get('price'))} <span class="mut">({esc(asof.get('as_of'))})</span></div>
      <div>Statements: {esc(asof.get('statements'))}</div>
      <div>News: {esc(asof.get('news'))}</div></div>"""
    basis_warn = "".join(f"<span class='chip'>⚠ {esc(w)}</span>"
                         for w in (basis.get("warnings") or []))
    checks_html = ""
    if failed:
        items = "".join(f"<li>{esc(c['detail'])}</li>" for c in failed)
        checks_html = (f"<div class='alert'><b>⚠ {len(failed)} consistency check(s) failed — "
                       f"the affected fields are withheld (—)</b><ul>{items}</ul></div>")

    # ── setup score ──────────────────────────────────────────────────────────
    score_html = ""
    if score:
        rows = "".join(
            f"<tr><td>{esc(c['component'].replace('_', ' '))}"
            + (f" <span class='basis'>n={c['sample_n']}</span>" if c.get('sample_n') is not None else "")
            + "</td>"
            f"<td class='v'>{num(c['value']) if c['available'] else esc(c.get('status') or 'no data')}</td>"
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

    # rows exist only where they apply (report_view): a fund has no company
    # fundamentals, a bank no EV / FCF — each card lists whatever is there
    kind = a.get("security_kind") or rv.get("security_kind") or "stock"
    if kind == "fund" or any(f.get("group") == "fund" for f in F.values()):
        fund_html = _fcard("Fund (at the report price)", F, FUND_ROWS)
    else:
        fund_html = _fcard("Fundamentals (at the report price)", F, FUNDAMENTAL_ROWS)
    opt_html = _fcard("Options positioning", F,
                      ["put_call", "atm_iv", "implied_move", "implied_vs_band"])

    hf = F.get("headlines") or {}
    items = hf.get("items") or []
    if items:
        head_html = "<ul>" + "".join(
            f"<li>{esc(h['headline'])} <span class='mut'>({esc(h.get('source'))}"
            f"{', ' + news_relevance.ny_date(h['ts']).strftime('%b %d') if h.get('ts') else ''})</span></li>"
            for h in items[:6]) + "</ul>"
    else:
        head_html = (f"<p class='mut'>No headlines about {esc(sym)} in the feed window "
                     f"({esc(hf.get('basis') or '')}); {esc(hf.get('display') or '')}.</p>")
    news_html = _fcard("News", F, ["move_cause", "regulatory", "news_sentiment", "claude_news"],
                       extra=f"<p class='mut' style='margin:8px 0 2px'>{esc(hf.get('display', ''))} · "
                             f"{esc(hf.get('basis', ''))}</p>{head_html}")
    ctx_html = _fcard("Context", F, [
        "beta", "correlation", "week52", "rv20", "social", "analyst_mix", "analyst_actions",
        "earnings_next", "position_value", "position_pl", "position_day_pl",
        "position_weight"])

    disc_html = ""
    if discussions:
        # OK / STALE / INVALID / SUPERSEDED per note — the same judgment the research prompt
        # hands the next session (analysis/research_quality.py)
        statuses = research_quality.prior_note_status(
            discussions, a.get("move"), basis, a.get("big_move_sessions"))
        items_html = ""
        for d, st in zip(discussions, statuses):
            tags = " ".join(f"<span class='tag'>{esc(t)}</span>" for t in (d.get("tags") or []))
            when = (d.get('created_ts') or '')[:10]
            note = (f"<div class='dated'>Written {esc(when)} — figures and facts in this note "
                    "are as of that date, not the report date.</div>")
            if st["status"] == research_quality.INVALID:
                note += (f"<span class='chip bad'>⚠ INVALID — {esc(st['reason'])}; "
                         "do not build on this note</span>")
            elif st["status"] == research_quality.SUPERSEDED:
                note += (f"<span class='chip bad'>⚠ SUPERSEDED — {esc(st['reason'])}; "
                         "do not build on this note</span>")
            elif st["status"] == research_quality.STALE:
                note += (f"<span class='chip'>⚠ STALE — {esc(st['reason'])}; it cannot "
                         "reflect that move</span>")
            items_html += f"""
            <div class="discussion">
              <h3>{esc(d.get('summary') or 'Discussion')} <span class="mut">· {esc((d.get('created_ts') or '')[:16].replace('T', ' '))}</span> {tags}</h3>
              {note}
              <div class="md">{md(d.get('response_markdown') or '')}</div>
            </div>"""
        disc_html = f"<div class='card page-break'><h2>Claude research discussions ({len(discussions)})</h2>{items_html}</div>"

    dec_html = ""
    if decisions:
        rows = "".join(
            f"<tr><td>{esc(d['created_ts'][:10])}</td><td>{esc(d['decision']).upper()}</td>"
            f"<td class='v'>{money(d['decision_price'])}</td><td class='v'>{money(d['current_price'])}</td>"
            f"<td class='v'>{num(d['pct_since'], '%')}</td></tr>" for d in decisions)
        dec_html = f"""<div class="card"><h2>Recorded decisions</h2>
        <table><thead><tr><th>When</th><th>Call</th><th>Price then</th><th>At report price</th><th>Since</th></tr></thead>
        <tbody>{rows}</tbody></table></div>"""

    cov_line = (f"This exact band construction held {cov.get('covered_pct')}% of "
                f"{cov.get('days'):,} backtested days"
                + (" (basket-wide figure)" if cov.get("scope") == "basket_overall" else "")
                if cov else "")

    stance_f = F.get("claude_stance") or {}
    stance_html = ""
    if stance_f.get("value"):
        # what would change the stance (prompt v5): conditions, review date,
        # confidence — written by the research session, shown as written
        cond = []
        if stance_f.get("upgrade_if"):
            cond.append("Upgrade if: " + "; ".join(stance_f["upgrade_if"]))
        if stance_f.get("downgrade_if"):
            cond.append("Downgrade if: " + "; ".join(stance_f["downgrade_if"]))
        meta = [f"review by {stance_f['review_by']}" if stance_f.get("review_by") else "",
                f"confidence {stance_f['confidence']}" if stance_f.get("confidence") else ""]
        if any(meta):
            line = " · ".join(m for m in meta if m)
            cond.append(line[0].upper() + line[1:])
        cond_html = "".join(f"<div class='cond'>{esc(c)}</div>" for c in cond)
        stance_html = (f"<div><b>Research stance ({esc(str(stance_f.get('as_of') or '')[:10])})</b>"
                       f"{esc(stance_f['display'])}{_chips(stance_f)}{cond_html}</div>")
    elif stance_f.get("warnings"):
        # no stance to show, but something about it needs saying (for example
        # the newest note's stance could not be read)
        stance_html = f"<div><b>Research stance</b>none shown{_chips(stance_f)}</div>"
    ah = F.get("after_hours") or {}
    ah_html = (f"<div class='mut'>{esc(ah['display'])}</div>" if ah.get("value") else "")
    band_f, sig_f, earn_f = F.get("band_next") or {}, F.get("day_move_sigma") or {}, F.get("earnings_next") or {}

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
  .basis {{ color:var(--mut); font-size:11px; }}
  .price {{ font-size:24px; font-weight:700; }} .up {{ color:#059669; }} .down {{ color:#dc2626; }}
  .strip {{ display:flex; gap:28px; flex-wrap:wrap; margin:14px 0 6px; }}
  .strip b {{ display:block; font-size:11px; text-transform:uppercase; color:var(--mut); }}
  .asof {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px;
           padding:8px 12px; margin:12px 0; font-size:12.5px; }}
  .asof b {{ font-size:11px; text-transform:uppercase; color:var(--mut); }}
  .alert {{ border:1px solid #f59e0b; background:#fffbeb; border-radius:8px; padding:8px 12px; margin:10px 0; }}
  .chip {{ display:inline-block; font-size:11px; color:#92400e; background:#fef3c7;
           border:1px solid #fcd34d; border-radius:6px; padding:1px 6px; margin:2px 4px 2px 0; }}
  .chip.bad {{ color:#991b1b; background:#fee2e2; border-color:#fca5a5; }}
  .cond {{ font-size:11.5px; color:var(--mut); }}
  .chiprow td {{ border-bottom:1px solid var(--line); padding-top:0; }}
  .dated {{ font-size:11.5px; color:var(--mut); font-style:italic; }}
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
    <div class="brand">TICKERBANDS · RESEARCH SNAPSHOT</div>
    <h1>{esc(sym)} <span class="mut">{esc(q.get('name') or '')}</span></h1>
    <div class="mut">Generated {esc(now)} · snapshot {esc(a.get('context_hash'))}</div>
  </div>
  <div style="text-align:right">
    <div class="price">{disp('price')}</div>
    <div class="{chg_cls}">{disp('day_move')}</div>
    {ah_html}
  </div>
</div>
{asof_html}
{basis_warn}
{checks_html}
<div class="strip">
  <div><b>{esc(band_f.get('label') or '80% expected range')}</b>{esc(band_f.get('display', '—'))}{_chips(band_f)}</div>
  <div><b>Move in σ</b>{esc(sig_f.get('display', '—'))}{_chips(sig_f)}</div>
  <div><b>Next earnings</b>{esc(earn_f.get('display', '—'))}{_chips(earn_f)}</div>
  {stance_html}
</div>
{f'<p class="mut" style="margin:2px 0 8px">{esc(cov_line)}</p>' if cov_line else ''}
{_sparkline_svg(a.get('chart') or [], a.get('band'))}

{score_html}
<div class="grid">
{fund_html}{opt_html}{news_html}{ctx_html}
</div>
{dec_html}
{disc_html}

<footer>
  TickerBands research &amp; education report — not investment advice. This tool
  does not predict price direction: across ~9,600 backtested predictions,
  1-day direction from these signals performed at ~coin-flip. The expected
  range is the only calibrated quantity shown{f" ({esc(cov_line)})" if cov_line else ""}.
  Every figure above is computed at one report price ({esc(asof.get('price'))}) and
  cross-checked; "—" with a ⚠ note means a figure was withheld rather than shown wrong.
  Field definitions: docs/REPORT_FIELDS.md · Generated by TickerBands.
</footer>
<script>setTimeout(() => window.print(), 700)</script>
</body></html>"""


def build_discover_html(candidates: list[dict]) -> str:
    """Short printable digest of Discover candidates — same design language as
    the analysis report. One compact card per candidate: evidence sources,
    the reasons the screens flagged it, cap, and status. Deliberately brief:
    these are STARTING POINTS for research, and the page says so."""
    doc_name = f"TickerBands_Discover_{dt.datetime.now().strftime('%Y-%m-%d_%H%M')}"
    n_conv = sum(1 for c in candidates if len(c.get("sources") or []) >= 2)
    all_sources = sorted({s for c in candidates for s in (c.get("sources") or [])})

    cards = []
    for c in candidates:
        cap = c.get("market_cap_m")
        cap_txt = (f"${cap/1000:.1f}B" if cap and cap >= 1000
                   else f"${cap:.0f}M" if cap else "cap n/a")
        tags = " ".join(f'<span class="tag">{esc(s)}</span>'
                        for s in (c.get("sources") or []))
        reasons = "".join(f"<li>{esc(rr)}</li>" for rr in (c.get("reasons") or [])[:4])
        status = c.get("status") or "new"
        conv = (' <span class="tag" style="color:#0891b2;border-color:#0891b2">'
                'convergence</span>' if len(c.get("sources") or []) >= 2 else "")
        cards.append(f"""<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <h3 style="margin:0;font-size:17px">{esc(c["symbol"])}
      <span class="mut" style="font-size:12px">{esc(cap_txt)}</span></h3>
    <div>{tags}{conv} <span class="tag">{esc(status)}</span></div>
  </div>
  <ul style="margin:6px 0 2px 18px;font-size:13px">{reasons}</ul>
  <p class="mut" style="margin:4px 0 0;font-size:11px">
    first seen {esc(str(c.get("first_seen") or "?"))} ·
    last seen {esc(str(c.get("last_seen") or "?"))}</p>
</div>""")

    body = ("\n".join(cards) if cards else
            '<p class="mut">No active candidates — run a scan from the Discover tab.</p>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{doc_name}</title>
<style>
  :root {{ --accent:#0891b2; --mut:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ font:14px/1.55 -apple-system, 'Segoe UI', sans-serif; color:#111827;
         max-width:760px; margin:0 auto; padding:28px; }}
  h1 {{ margin:0; font-size:24px; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:3px solid var(--accent); padding-bottom:12px; }}
  .brand {{ color:var(--accent); font-weight:700; }}
  .mut {{ color:var(--mut); font-weight:400; font-size:12px; }}
  .strip {{ display:flex; gap:28px; flex-wrap:wrap; margin:14px 0 6px; }}
  .strip b {{ display:block; font-size:11px; text-transform:uppercase; color:var(--mut); }}
  .card {{ border:1px solid var(--line); border-radius:10px; padding:12px 16px;
           margin:10px 0; break-inside:avoid; }}
  .tag {{ font-size:10px; border:1px solid var(--line); border-radius:6px;
          padding:1px 6px; color:var(--mut); }}
  footer {{ margin-top:22px; border-top:1px solid var(--line); padding-top:10px;
            font-size:11px; color:var(--mut); }}
</style></head><body>
<div class="head">
  <div><h1>Discover digest</h1>
    <span class="mut">{dt.datetime.now().strftime('%B %d, %Y · %H:%M')}</span></div>
  <div class="brand">TickerBands</div>
</div>
<div class="strip">
  <div><b>Candidates</b>{len(candidates)}</div>
  <div><b>Convergence (≥2 sources)</b>{n_conv}</div>
  <div><b>Evidence types</b>{esc(", ".join(all_sources) or "—")}</div>
</div>
{body}
<footer>
  Discover candidates are STARTING POINTS surfaced by evidence screens
  (insider clusters, short-interest shifts, earnings surprises, social/web
  sweeps) — deliberately excluding mega-cap mainstays. They are not
  recommendations and carry no directional prediction. Research &amp;
  education only · Generated by TickerBands.
</footer>
<script>setTimeout(() => window.print(), 700)</script>
</body></html>"""
