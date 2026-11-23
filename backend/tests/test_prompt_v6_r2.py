"""
Research prompt v6, round 2 (docs/VERDICT_SPEC.md rule changes S1–S8 and the
fixes from three real v6 runs). Pins: the less favorable of two app grades,
price to sales and the 25% peer rule for loss-makers, the inverted grades of a
yield measure, the keyed soft-signal rules and the <snapshot> Rule checks from
`rule_inputs`, the new-listing cap from `listed_under_a_year`, the order of the
fixed steps and the unrated prefixes, the wider watch-only list, move_cause for
re-checked sessions, the options columns, unrated conditions, the incremental
word target, the fund wording, trigger figures, the fallback regulator, the
updated examples and the style guards on every new variant. Tickers, dates
and figures are fictional or illustrative.
"""
import re

import frontmatter
import pytest

from api import research_prompt as rp
from tests import replay_fixtures as rf
from tests.test_prompt_fixes_1122 import _guards
from tests.test_prompt_v6_verdict import VR, _step5, _task, _group, _no_range
from tests.test_research_prompt_v5 import (V4_SNAPSHOT_CHARS, _analyze, _block, _examples,
                                           _prompt)


@pytest.fixture()
def base(tmp_db, monkeypatch):
    """NVDA replay (Sat 2026-11-21), owned, fresh statements, no notes."""
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


NORM = {**VR, "now": 20.0, "grade_hint": "cheap", "normalized": True, "now_normalized": 52.0,
        "normalized_grade_hint": "fair", "warnings": []}

STOCK_KEYS = ("revenue_decline", "op_margin_decline", "fcf_negative", "receivables_vs_revenue",
              "inventory_vs_revenue", "ni_over_fcf", "net_debt_ebitda", "interest_coverage",
              "dilution", "guidance_cut", "dividend_cut", "exec_exit", "insider_selling",
              "payout_ffo", "occupancy", "reit_leverage")
FUND_KEYS = ("distribution_cut", "roc_nav", "nav_vs_index", "fee_increase", "assets_decline",
             "nav_discount")
# a bank also takes the two stock rules the app computes for it (spec §6b)
BANK_KEYS = ("npl_rise", "charge_offs", "capital_ratio", "deposit_decline", "nim_decline",
             "efficiency_ratio", "cre_concentration", "revenue_decline", "dilution")
ALL_KEYS = set(STOCK_KEYS + FUND_KEYS + BANK_KEYS)

# the shape of spec §6b: a dict with the rules, the two quarters newest first
RULES = {"kind": "stock", "as_of": "2026-10-26", "filed": "2026-11-19", "rules": [
    {"key": "ni_over_fcf", "label": "Net income above free cash flow by more than 25%",
     "values": [{"period": "2026-10-26", "net_income_adj": 3.19e10, "fcf": 2.15e10, "ratio": 1.48},
                {"period": "2026-07-27", "net_income_adj": 2.64e10, "fcf": 1.04e10, "ratio": 2.53}],
     "threshold": "> 1.25 in both quarters", "fires": True,
     "basis": "SEC 10-Q quarters", "warnings": []},
    {"key": "revenue_decline", "values": [], "threshold": "< 0% in both", "fires": False,
     "basis": "SEC 10-Q quarters", "warnings": []},
    {"key": "op_margin_decline", "fires": False, "warnings": []},
    {"key": "fcf_negative", "fires": False},
    {"key": "receivables_vs_revenue", "values": [{"period": "2026-10-26", "gap_pts": 8.1},
                                                 {"period": "2026-07-27", "gap_pts": 37.0}],
     "threshold": "> 20 points in both", "fires": False, "warnings": ["Q2 alone above 20 points"]},
    {"key": "inventory_vs_revenue", "fires": False},
    {"key": "net_debt_ebitda", "fires": False},
    {"key": "interest_coverage", "fires": None, "warnings": ["interest expense not filed"]},
    {"key": "dilution", "fires": None, "warnings": []},
]}


def _s5a(p):
    s = _step5(p)
    return s[s.index("   a. Value grade."):s.index("   b. Risk items.")]


def _s5c(p):
    s = _step5(p)
    return s[s.index("   c. Default rules"):s.index("   d. The fixed steps")]


def _s5d(p):
    s = _step5(p)
    return s[s.index("   d. The fixed steps"):s.index("   e. The user's rules")]


def _rules_group(p):
    s = _block(p, "snapshot")
    if "\nRule checks — " not in s:
        return None
    return s[s.index("\nRule checks — ") + 1:].split("\n\n")[0]


# ── S1: the less favorable of two app grades ────────────────────────────────

def test_less_favorable_and_the_grade_read_defensively():
    lf = rp.less_favorable
    assert lf("cheap", "fair") == "fair" and lf("fair", "expensive") == "expensive"
    assert lf("cheap", "expensive") == "expensive" and lf("cheap", None) == "cheap"
    assert lf(None, None) is None and lf("bogus", "fair") == "fair"
    vr = rp.valuation_range
    assert vr({"valuation_range": NORM})["grade"] == "fair"               # derived
    assert vr({"valuation_range": {**NORM, "grade": "Expensive"}})["grade"] == "expensive"
    assert vr({"valuation_range": {**NORM, "grade": "junk"}})["grade"] == "fair"
    assert vr({"valuation_range": VR})["grade"] == "cheap"                # one hint
    assert vr({"valuation_range": {**VR, "grade_hint": None}})["grade"] is None


def test_snapshot_states_the_resulting_grade_and_step_5a_takes_the_less_favorable(base):
    p = _prompt({**base, "valuation_range": NORM})
    g = _group(p)
    assert ("app grades: cheap on the reported figure, fair on the five-year average "
            "(52.0x) → fair (the less favorable)") in g, g
    a = _s5a(p)
    assert ("When <snapshot> shows two app grades (one on the reported figure, one on the "
            "five-year average — stated when current profit or cash flow is more than double "
            "that average), take the less favorable of the two; depart from it only with a "
            "stated reason in `value_basis`.") in a
    assert "<snapshot> gives it under Valuation vs own history, with the app's grade" in a
    for gone in ("Choose one", "grade on the average", "cycle peak", "the reported figure when"):
        assert gone not in a, gone
    # a payload `grade` the app sends is shown as it is
    g = _group(_prompt({**base, "valuation_range": {**NORM, "grade": "cheap"}}))
    assert " → cheap" in g and "(the less favorable)" not in g


# ── S2: loss-makers and the peer rule ───────────────────────────────────────

def test_loss_maker_uses_price_to_sales_and_peers_have_thresholds(base):
    a = _s5a(_prompt({**base, "valuation_range": VR}))
    assert ("where those are negative too (a loss-making company), price to sales") in a
    assert ("A loss-making company (price to free cash flow and P/E both negative) is graded "
            "on price to sales: against its own history where <snapshot> gives it, else "
            "against named peers from your research (name each, with its figure and source): "
            "cheap = at least 25% below the peer median, expensive = at least 25% above it, "
            "fair otherwise.") in a
    assert ("Paused is only for data problems: the measure cannot be computed reliably "
            "(inputs withheld, or share count or currency unreconciled, as with some ADRs) — "
            "negative earnings are not one.") in a
    # without a range the peer rule is stated once, in the source sentence
    peers = _s5a(_prompt(_no_range(base)))
    assert peers.count("25% below the peer median") == 1
    assert "else against named peers, as above." in peers
    for kind in ("fund", "bank"):
        assert "price to sales" not in _s5a(_prompt({**base, "security_kind": kind}))


def test_p_sales_range_renders_as_a_multiple(base):
    g = _group(_prompt({**base, "valuation_range": {**VR, "measure": "p_sales",
                                                    "label": "Price to sales", "now": 9.4}}))
    assert g.startswith("Valuation vs own history — Price to sales\n- now 9.4x (TTM through")


# ── S3: a yield measure reads the other way round ───────────────────────────

def test_fund_grades_are_inverted_and_have_no_two_grade_clause(base):
    a = _s5a(_prompt({**base, "security_kind": "fund"}))
    assert ("A distribution rate reads the other way round (a higher rate is a lower price), so "
            "on its own history: cheap = now at least 15% above its five-year median; expensive "
            "= below its five-year low or under two-thirds of the median; fair = in between.") in a
    assert "cheap = a rate at least 25% above the peer median" in a
    assert "two app grades" not in a and "profit or cash flow" not in a
    assert "Paused is only for data problems: the measure cannot be computed reliably " \
           "(inputs withheld)." in a and "negative earnings" not in a
    s = _s5a(_prompt(base))
    assert ("On its own history: cheap = now at least 15% below its five-year median; expensive "
            "= above its five-year high or more than 1.5× the median; fair = in between.") in s


# ── S4: keyed soft signals and the app's rule checks ────────────────────────

@pytest.mark.parametrize("kind,keys,eg", [("stock", STOCK_KEYS, "ni_over_fcf"),
                                          ("fund", FUND_KEYS, "distribution_cut"),
                                          ("bank", BANK_KEYS, "npl_rise")])
def test_each_rule_carries_its_key_and_entries_start_with_it(base, kind, keys, eg):
    c = _s5c(_prompt({**base, "security_kind": kind}))
    for k in keys:
        assert f"`{k}`" in c, k
    for k in ALL_KEYS - set(keys):
        assert f"`{k}`" not in c, k
    assert (f"Start each `soft_signals` entry with its rule key in square brackets, for example "
            f"\"[{eg}] …\".") in c


def test_stock_rules_have_exact_thresholds(base):
    c = _s5c(_prompt(base))
    for txt in ("over the last two reported quarters (a quarter reported only in the latest earnings release counts as reported), holding in both unless stated",
                "`fcf_negative` free cash flow negative (skipped when revenue grows more than "
                "25% year on year)",
                "`ni_over_fcf` GAAP net income, less flagged one-time gains and tax benefits, "
                "more than 25% above free cash flow",
                "`net_debt_ebitda` net debt above 3× trailing EBITDA and higher than a year earlier",
                "`dilution` diluted share count up more than 5% year on year (not in the first "
                "four quarters after a listing)",
                "`exec_exit` abrupt CEO or CFO exit, counting for 12 months or until a permanent "
                "successor is named",
                "three or more insiders selling outside 10b5-1 plans within 90 days"):
        assert txt in c, txt
    assert "well above" not in c and "a year ·" not in c


def test_rule_checks_render_compactly_from_the_payload(base):
    p = _prompt({**base, "rule_inputs": RULES})
    g = _rules_group(p)
    assert g == (
        "Rule checks — app arithmetic, filings through 2026-11-19\n"
        "- fires: [ni_over_fcf] 2026-10-26: net income adj $31.9B, fcf $21.5B, ratio 1.48, "
        "2026-07-27: net income adj $26.4B, fcf $10.4B, ratio 2.53\n"
        "- does not fire: revenue_decline, op_margin_decline, fcf_negative, "
        "receivables_vs_revenue (Q2 alone above 20 points), inventory_vs_revenue, "
        "net_debt_ebitda\n"
        "- not computed (interest expense not filed): interest_coverage\n"
        "- not computed: dilution"), g
    c = _s5c(p)
    assert ("<snapshot> Rule checks gives the app's arithmetic for the rules it could compute: "
            "follow it where it says fires or does not fire, unless a filed figure shows it "
            "wrong (then say so, with that figure); check the rest — those not computed and "
            "every rule it does not list — from your research.") in c
    assert "The app sent no rule checks" not in c
    # a list form and a precomputed summary read the same way
    g2 = _rules_group(_prompt({**base, "rule_inputs": [
        {"key": "dilution", "summary": "diluted shares +6.2% and +5.8% y/y", "fires": True,
         "threshold": "> 5% in both"}]}))
    assert g2 == ("Rule checks — app arithmetic, filed statements\n"
                  "- fires: [dilution] diluted shares +6.2% and +5.8% y/y")


def test_no_rule_checks_without_the_payload_and_junk_is_read_defensively(base):
    p = _prompt({k: v for k, v in base.items() if k != "rule_inputs"})
    assert _rules_group(p) is None
    assert "The app sent no rule checks, so check each rule from the filings and the latest " \
           "release." in _s5c(p)
    for junk in (None, "x", 5, [], {"rules": "x"}, {"rules": [None, {"key": "Bad Key"}, {}]},
                 {"status": "no_data", "rules": [], "warnings": ["not computed"]}):
        q = _prompt({**base, "rule_inputs": junk})
        assert _rules_group(q) is None, junk
        assert "The app sent no rule checks" in _s5c(q)
        assert "None" not in q
    q = _prompt({**base, "rule_inputs": [{"key": "dilution", "fires": "yes", "values": [None]}]})
    assert _rules_group(q).endswith("- not computed: dilution")


def test_snapshot_length_with_rule_checks_and_both_grades(tmp_db, monkeypatch):
    from tests.test_research_prompt_v5 import _seed_nvda_notes
    _seed_nvda_notes()
    a = _analyze(monkeypatch, rf.nvda_providers(fresh_statements=False), rf.NVDA_NOW, "NVDA")
    # as the app sends it: a short summary of the two quarters beside the values
    ri = {**RULES, "rules": [{**r, "summary": "2026-10-26 ratio 1.48, 2026-07-27 ratio 2.53"}
                             if r["key"] == "ni_over_fcf" else r for r in RULES["rules"]]}
    def size(q):
        return len(_block(q, "data_caveats")) + len(_block(q, "snapshot"))
    p = _prompt({**a, "valuation_range": NORM, "rule_inputs": ri})
    without = _prompt({**a, "valuation_range": NORM})
    # the v5 budget holds without rule checks; nine rule checks (one firing,
    # one qualified, two not computed) add a compact group, net of the value
    # lens's net-income flag they replace
    assert size(without) < 0.75 * V4_SNAPSHOT_CHARS, size(without)
    assert size(p) - size(without) < 250, size(p) - size(without)
    assert size(p) < 0.78 * V4_SNAPSHOT_CHARS, size(p)
    # the value lens's net-income flag gives way to the computed rule (stated once)
    assert "net income materially exceeds free cash flow" not in _block(p, "snapshot")
    assert "net income materially exceeds free cash flow" in _block(_prompt(a), "snapshot")


# ── S5: the new-listing cap follows listed_under_a_year ─────────────────────

def test_cap_follows_listed_under_a_year_and_the_sigma_caveat_follows_is_new_listing(base):
    cap = "(6) last, as a listing under a year old, no better than fair"
    caveat = "The price history is too short for the app to measure a move in σ"
    p = _prompt({**base, "listed_under_a_year": True, "is_new_listing": False})
    assert cap in _s5d(p) and caveat not in p
    p = _prompt({**base, "listed_under_a_year": False, "is_new_listing": True})
    assert cap not in _s5d(p) and caveat in p
    # fallback: is_new_listing when the key is absent; funds never capped
    assert cap in _s5d(_prompt({**base, "is_new_listing": True}))
    assert cap in _s5d(_prompt({**base, "security_kind": "bank", "listed_under_a_year": True}))
    assert cap not in _s5d(_prompt({**base, "security_kind": "fund",
                                    "listed_under_a_year": True}))
    assert rp.listed_under_a_year({"listed_under_a_year": "yes", "is_new_listing": False}) is False


# ── S6: the order of the fixed steps and the unrated prefixes ───────────────

def test_fixed_steps_in_spec_order_and_unrated_prefixes(base):
    d = _s5d(_prompt(base))
    order = ["(1) research_status \"failed\" or a required search not run",
             "(2) any hard flag → avoid, even when the research failed or the value grade is "
             "paused", "(3) three or more soft signals → avoid",
             "(4) value grade paused, or too little clean data → unrated",
             "(5) the baseline from the value grade"]
    pos = [d.index(o) for o in order]
    assert pos == sorted(pos)
    assert ("An unrated `verdict_note` starts \"research incomplete\" when the research is "
            "incomplete and \"value paused\" when the value grade is paused.") in d
    ev = rp.expected_verdict
    assert ev("cheap", 1, 0, "failed") == "avoid" and ev("paused", 1, 0) == "avoid"
    assert ev("paused", 0, 3) == "avoid" and ev("paused", 0, 2) == "unrated"
    assert ev("cheap", 0, 0, "failed") == "unrated" and ev("cheap", 0, 3, "failed") == "unrated"
    assert ev("cheap", 0, 2, new_listing_cap=True) == "fair"
    assert ev("cheap", 0, 0, new_listing_cap=True) == "fair"


# ── S7 / S8: watch-only, hard, and the risk grade ───────────────────────────

def test_watch_only_list_and_the_hard_threshold(base):
    b = _step5(_prompt(base))
    for txt in ("an item whose only sources could not be opened (written \"not verified\": it "
                "may sit here, not in the other two lists)",
                "open investigations, subpoenas, civil investigative demands, requests for "
                "testimony and inquiries (even confirmed ones — an investigation is not a finding)",
                "a regulatory or legal item is hard only with charges or a complaint filed by a "
                "regulator, a consent order, a Wells notice or the company's own filing "
                "confirming the finding",
                "watch with two soft signals, or with one or none and at least one watch item"):
        assert txt in b, txt
    assert "enforcement action" not in rp.HARD_COMMON
    assert "sponsor" not in b
    f = _step5(_prompt({**base, "security_kind": "fund"}))
    assert "lawsuits or regulator matters about the fund's sponsor rather than the fund itself" in f
    assert rp.expected_risk(0, 0, 1) == "watch" and rp.expected_risk(0, 1, 1) == "watch"


# ── 9. move_cause for a re-checked session ──────────────────────────────────

def test_move_cause_counts_a_rechecked_session_as_found(tmp_db, monkeypatch):
    from tests.test_research_prompt_v5 import V5_META, _nvda_monday_evening
    rf.add_discussion("NVDA", "2026-11-21T10:05:00", "Probe is the reported cause",
                      news_view="bearish", news_note="Nov 16–21: probe reported (two sources)",
                      stance="hold", stance_horizon="3-6mo", stance_note="thesis intact",
                      research_meta={**V5_META, "price_used": 125.53, "review_by": "2027-01-15",
                                     "news_window": "2026-11-16..2026-11-21"})
    prov, now = _nvda_monday_evening()
    p = _prompt(_analyze(monkeypatch, prov, now, "NVDA"))
    assert "once re-checked, it counts as found for `move_cause`." in p
    out = p[p.index("## Output contract"):]
    assert ("`move_cause` is \"found\" when every session step 1 lists as still needing a cause "
            "or as already explained (once re-checked) has one") in out


def test_move_cause_plain_without_an_explained_session(base):
    p = _prompt(base)
    assert "already explained (once re-checked)" not in p and "once re-checked" not in p


# ── 10–11. options columns and unrated conditions ───────────────────────────

def test_options_columns_and_unrated_rows(base):
    t = _task(_prompt(base))
    s6 = t[t.index("6. Lay out the options"):t.index("7. Check your work")]
    assert ("Columns for each row: \"verdict supports it\" (yes or no, from that default "
            "meaning; for an unrated verdict every row reads \"the verdict gives no guidance\"); "
            "\"your rules\" — only where the user's notes or account rules are already "
            "available to you") in s6
    assert "so a hold the user's rules call for is shown as theirs and not as unsupported" in s6
    assert "Without such rules, leave the \"your rules\" column out" in s6
    assert "no row" not in s6


def test_unrated_keeps_review_by_and_may_drop_downgrade_if(base):
    p = _prompt(base)
    d = _s5d(p)
    assert "a `review_by` date for every verdict" in d
    assert ("An unrated verdict's `upgrade_if` says what would let it be rated; its "
            "`downgrade_if` may be left out.") in d
    assert "`downgrade_if` (on an unrated verdict only)" in p[p.index("## Output contract"):]
    failed = frontmatter.loads(_examples(p)[2]).metadata
    assert failed["verdict"] == "unrated" and failed["review_by"]
    assert "downgrade_if" not in failed and "rated" in failed["upgrade_if"][0]


# ── 12. incremental word target ─────────────────────────────────────────────

def test_incremental_note_aims_for_700_words(tmp_db, monkeypatch):
    from tests.test_prompt_fixes_1122 import _fresh_note
    _fresh_note()
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    assert "On this incremental run aim for about 700 words" in p
    assert "500 words" not in p


# ── 13. fund wording ────────────────────────────────────────────────────────

def test_fund_distribution_cut_tracking_and_single_tracker_fallback(base):
    p = _prompt({**base, "security_kind": "fund"})
    c = _s5c(p)
    assert ("`distribution_cut` latest distribution more than 10% below the 3-month average "
            "(monthly payers) or the average of the last four payments (quarterly and other "
            "payers)") in c
    assert "6-month average" not in c
    assert ("`nav_vs_index` NAV total return trailing its index by more than 3 points on the "
            "latest published 12-month figure") in c
    s1 = p[p.index("1. Research first"):p.index("2. Reconcile the data")]
    assert ("if no other ETF tracks it, compare over the period the issuer publishes, and say "
            "so), the latest published 12-month NAV return against the index") in s1


# ── 14–15. trigger figures and the fallback regulator ───────────────────────

def test_trigger_share_count_and_fallback_regulator(base):
    p = _prompt(base)
    s3 = p[p.index("3. Set and check the triggers"):p.index("4. Analyze")]
    assert ("(give the share price it corresponds to; the share count is market cap ÷ price, "
            "both in <snapshot>), and a bare price target does not; a trigger that needs a "
            "figure <snapshot> lacks says where that figure is published") in s3
    f = _prompt({**base, "security_kind": "fund"})
    assert "market cap ÷ price" not in f
    s1 = p[p.index("1. Research first"):p.index("2. Reconcile the data")]
    assert ("(the agency that licenses or polices its line of business; where none is obvious, "
            "the consumer and competition regulator — the FTC in the US)") in s1


# ── 16. examples ────────────────────────────────────────────────────────────

def test_examples_use_rule_keys_and_neutral_owner_notes(base):
    p = _prompt(base)
    for ex in _examples(p):
        m = frontmatter.loads(ex).metadata
        for s in m.get("soft_signals") or []:
            k = re.match(r"\[([a-z_]+)\] ", s)
            assert k and k.group(1) in ALL_KEYS, s
        assert m["verdict"] == rp.expected_verdict(
            m["value_grade"], len(m.get("hard_flags") or []), len(m.get("soft_signals") or []),
            m["research_status"])
    owned = frontmatter.loads(_examples(p)[0]).metadata
    assert owned["soft_signals"][0].startswith("[op_margin_decline] ")
    assert owned["soft_signals"][1].startswith("[net_debt_ebitda] ")
    assert "up from 2.6x a year earlier" in owned["soft_signals"][1]
    for gone in ("company-confirmed investigation", "buying freeze"):
        assert gone not in p, gone
    assert "\"your rule — trim above 10% of the account — is not met: hold\"" in _step5(p)
    assert "<[rule key] rulebook signal" in p


def test_examples_pass_the_apps_checks(base, tmp_path):
    """The examples go through the real parser and the app's verdict checks
    with no flag (research_quality.verdict_flags)."""
    from analysis import research_quality as rq
    from tests.test_prompt_v6_verdict import _parse
    for ex in _examples(_prompt(base)):
        row, meta = _parse(tmp_path, ex)
        flags = rq.verdict_flags({**row, "research_meta": meta})
        assert flags == [], (meta.get("ticker"), flags)


# ── style guards on the new variants ────────────────────────────────────────

@pytest.mark.parametrize("kind", ["stock", "fund", "bank"])
@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("vr", [None, "norm", "p_sales"])
@pytest.mark.parametrize("rules", [None, "dict"])
def test_style_guards_on_round_2_variants(base, kind, listed, vr, rules):
    a = {**_no_range(base), "security_kind": kind, "listed_under_a_year": listed}
    if vr == "norm":
        a["valuation_range"] = NORM
    elif vr == "p_sales":
        a["valuation_range"] = {**VR, "measure": "p_sales", "label": "Price to sales"}
    if rules:
        a["rule_inputs"] = RULES
    p = _prompt(a)
    _guards(p)
    for bad in ("None", "{", "}"):
        assert bad not in p, bad
    assert not [ln for ln in p.splitlines() if ln != ln.rstrip()]
