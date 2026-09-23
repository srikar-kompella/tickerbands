"""
Value-lens acceptance (Everything Money PDF incorporation, 2026-09-22):
metrics computed from statements, comparison READINGS + warning FLAGS, and
the analysis carried into the Claude prompt where the stance is made.
Constitutional check: nothing here feeds the Setup Score.
"""
from analysis.value_lens import build_value_lens


def _stmts(ni=1_000e6, fcf=900e6, divs=300e6, rev0=10_000e6,
           debt=2_000e6, cash=1_000e6, equity=5_000e6,
           ann_scale=1.0, rev_growth=0.10):
    annual = []
    for i in range(5):
        annual.append({
            "revenue": rev0 / ((1 + rev_growth) ** i),
            "net_income": ni * ann_scale / (1.05 ** i),
            "ebit": ni * 1.3 / (1.05 ** i), "eff_tax_rate": 0.21,
            "fcf": fcf * ann_scale / (1.05 ** i), "acquisitions": -10e6,
        })
    return {"available": True,
            "ttm": {"fcf": fcf, "net_income": ni, "dividends_paid": divs,
                    "ebit": ni * 1.3, "eff_tax_rate": 0.21},
            "annual": annual,
            "balance": {"total_debt": debt, "cash": cash, "equity": equity},
            "source": "test", "as_of": "2026-09-22"}


def test_core_metrics_and_bases():
    vl = build_value_lens(15_000e6, _stmts())
    assert abs(vl["p_fcf"]["value"] - 15_000 / 900) < 0.1
    assert vl["fcf_ttm"]["value"] == 900.0                    # $M
    assert abs(vl["payout_of_fcf_pct"]["value"] - 33.33) < 0.1
    assert vl["ev"]["value"] == 16_000.0                      # +2000 −1000 ($M)
    assert vl["net_cash"] is False
    assert 9.5 <= vl["rev_cagr_3y"]["value"] <= 10.5
    assert "FY" in vl["pe_avg"]["basis"]                      # honest span label
    assert vl["roic_ttm"]["approx"] is True


def test_ni_exceeds_fcf_flag():
    vl = build_value_lens(15_000e6, _stmts(ni=1_000e6, fcf=500e6))
    assert vl["ni_vs_fcf_flag"] is True
    assert any("accounting" in w for w in vl["warnings"])


def test_payout_over_half_fcf_flags_income_sleeve_check():
    vl = build_value_lens(15_000e6, _stmts(divs=600e6))
    assert any("50%" in w for w in vl["warnings"])


def test_net_cash_flag():
    vl = build_value_lens(15_000e6, _stmts(debt=500e6, cash=3_000e6))
    assert vl["net_cash"] is True
    assert any("net cash" in w.lower() for w in vl["warnings"])


def test_readings_fire_on_ttm_vs_avg_divergence():
    # TTM NI double the FY average → "sustainable or one-time?" reading
    vl = build_value_lens(15_000e6, _stmts(ann_scale=0.45))
    assert any("sustainable" in r for r in vl["readings"])


def test_unavailable_statements_return_none():
    assert build_value_lens(15_000e6, None) is None
    assert build_value_lens(15_000e6, {"available": False}) is None


def test_prompt_carries_lens_and_stance_mandate(tmp_db, mock_mode):
    from analysis import composer
    from api.routes import build_claude_prompt
    a = composer.analyze("NVDA")
    assert a["value_lens"] is not None                        # mock statements flow
    p = build_claude_prompt(a)
    assert "Statement value lens" in p
    assert "weigh these in your stance" in p
    assert "argue against it" in p                            # the mandate line


def test_value_lens_never_touches_setup_score(tmp_db, mock_mode):
    """Constitutional: score components are unchanged by the lens."""
    from analysis import composer
    a = composer.analyze("AAPL")
    comps = {c["component"] for c in a["setup_score"]["components"]}
    assert not any("roic" in c.lower() or "fcf" in c.lower() or "value" in c.lower()
                   for c in comps)
