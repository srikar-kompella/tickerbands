"""
discussions_svc/parser.py — markdown+frontmatter → claude_discussions row.

(Package named *_svc so it can't shadow the repo-root discussions/ folder the
watcher observes.)

The Ask-Claude prompt instructs Claude Code CLI to write:

    discussions/{TICKER}/{ISO_DATETIME}.md
    ---
    ticker: AAPL
    created_ts: 2026-07-13T14:32:15
    summary: One-line takeaway for the timeline
    tags: [earnings-review, technical]
    context_hash: sha256:abc123
    ---
    # body...

Humans and models both write files, so parsing is forgiving where safe
(missing summary → first body line; missing ticker → parent folder name; a
UTF-8 BOM, a wrapping ```markdown fence and a line or two of chat in front of
that fence are removed) and strict where lying would corrupt the timeline (no
body, a body that still starts with '---', a body that starts with a bare
code fence, or a body that still holds the fenced file → ParseError). A value the app cannot read is never dropped
silently when the research prompt requires it: it is recorded in
`research_meta.unreadable` and flagged beside the stance or the news check
(analysis/research_quality.py). Any other failure while reading a file is
raised as ParseError too, so the watcher always either ingests a file or
quarantines it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any

import frontmatter

from analysis.research_quality import parse_date
from db import connect


class ParseError(Exception):
    pass


_PLACEHOLDER = re.compile(r"^<[^<>]*>$")        # a template slot left unfilled: "<…>"
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})[ \t]*[A-Za-z0-9_-]*[ \t]*$")
_KEY_LINE = re.compile(r"[A-Za-z_][\w-]*:(\s|$)")
_BARE_FENCE = re.compile(r"^(`{3,}|~{3,})$")
PREAMBLE_LINES = 5      # chat lines tolerated in front of the fenced file
# a whole note still sitting inside the body — in a code fence or bare
_FENCED_FILE = re.compile(r"^(?:(?:`{3,}|~{3,})[^\n]*\n\s*)?---[ \t]*\n\s*ticker[ \t]*:", re.M)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _is_placeholder(v: Any) -> bool:
    return isinstance(v, str) and bool(_PLACEHOLDER.match(_unquote(v)))


def _drop_placeholders(meta: dict) -> dict:
    """Remove template slots the session left as "<…>" — scalars and list
    items alike. Stored, `upgrade_if: ["<dated, checkable condition>"]` would
    hide the no-conditions flag and a placeholder under `plan_triggers_met`
    would show as a met trigger."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if _is_placeholder(v):
            continue
        if isinstance(v, (list, tuple)):
            v = [x for x in v if not _is_placeholder(x)]
        out[k] = v
    return out


def _fenced_file_start(lines: list[str]) -> int | None:
    """Index of the fence line that opens a wrapped note: a code fence within
    the first PREAMBLE_LINES non-blank lines whose next non-blank line is
    '---' followed by a `key:` line. None when the text is not a wrapped
    note (a body that merely opens with a code block is left alone)."""
    seen = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        if _FENCE_OPEN.match(ln.strip()):
            rest = [x.strip() for x in lines[i + 1:] if x.strip()]
            # '---' right inside the fence is enough when nothing precedes
            # the fence; behind a chat line a `key:` line must follow too
            if rest and rest[0] == "---" and (
                    seen == 0 or (len(rest) > 1 and _KEY_LINE.match(rest[1]))):
                return i
            return None
        seen += 1
        if seen > PREAMBLE_LINES:
            return None
    return None


def _clean_text(text: str) -> str:
    """Undo what an editor or a chat UI adds around a note: a UTF-8 BOM; the
    ```markdown fence a claude.ai session wraps the file in; and a line of
    chat saved with it ("Here is the complete file to save:" before the
    fence, a sign-off after it). The fence is removed only when frontmatter
    starts right inside it; a body that merely opens with a code block is
    left alone."""
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    head = text.lstrip()
    lines = head.split("\n")
    at = _fenced_file_start(lines)
    if at is None:
        return head if head.startswith("---") else text
    fence = _FENCE_OPEN.match(lines[at].strip()).group(1)
    inner = lines[at + 1:]
    while inner and not inner[-1].strip():
        inner.pop()
    marks = [i for i, ln in enumerate(inner) if ln.strip().startswith(fence[0] * 3)]
    if inner and re.fullmatch(r"(`{3,}|~{3,})", inner[-1].strip()):
        inner = inner[:-1]                    # the wrapper's closing fence
    elif len(marks) % 2 == 1 and re.fullmatch(r"(`{3,}|~{3,})", inner[marks[-1]].strip()):
        # an odd number of fence lines: the last one closes the wrapper and
        # what follows it is chat, not part of the file
        inner = inner[:marks[-1]]
    return "\n".join(inner).lstrip("\n").rstrip() + "\n"


def _split_inline_list(inner: str) -> list[str]:
    """Split the inside of `[a, "b, c", d]` on commas that are outside
    quotes. A quote only opens at the start of an item, so an apostrophe
    inside plain text ("the user's stop") is ordinary text."""
    items: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'" and not "".join(buf).strip():
            quote, buf = ch, []
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    items.append("".join(buf).strip())
    return [x for x in items if x]


def _salvage_frontmatter(text: str) -> tuple[dict, str] | None:
    """Line-by-line fallback for frontmatter that strict YAML rejects.

    A research session following the prompt's own example wrote
    `news_note: Nov 14–21: SEC probe reported …` — an unquoted colon, which
    YAML reads as a nested mapping and rejects, silently dropping the whole
    analysis (found 2026-11-21). Each `key: value` line is split on the FIRST
    ": " only; `[a, "b, c"]` becomes a list (commas inside quotes stay in the
    item); surrounding quotes are removed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    meta: dict[str, Any] = {}
    last_key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith((" ", "\t", "-")) and last_key:   # continuation / list item
            item = _unquote(raw.strip().lstrip("-"))
            cur = meta.get(last_key)
            meta[last_key] = (cur if isinstance(cur, list) else
                              ([cur] if cur not in (None, "") else [])) + [item]
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if val.startswith("[") and val.endswith("]"):
            meta[key] = _split_inline_list(val[1:-1])
        else:
            meta[key] = _unquote(val)
        last_key = key
    known = {"ticker", "created_ts", "summary", "stance", "news_note", "news_view",
             "decision", "context_hash", "tags", "verdict"}
    if not known & set(meta):
        return None                       # nothing recognizable → still an error
    return meta, "\n".join(lines[end + 1:])


STANCES = {"buy", "sell", "hold", "trim", "watch"}
# words a session uses for the same call (prompt v5 options table: Hold / Add /
# Trim / Exit, Buy / Watch / Pass). On a name the user does not own, `sell`
# means avoid/pass — see docs/RESEARCH_PROMPT.md and analysis/scorecard.py.
STANCE_ALIASES = {"add": "buy", "exit": "sell", "pass": "sell", "avoid": "sell"}


def _stance(raw: Any) -> tuple[str | None, str | None]:
    """(stance, unreadable text). A word the app does not know is reported,
    not dropped — a silent drop left an older stance showing as current.
    A stance word still inside the template's brackets ("<hold>") is not a
    stance: it is reported as unreadable. The untouched template slot
    ("<buy|sell|hold|trim|watch>") is simply no stance."""
    if isinstance(raw, bool) or raw in (None, ""):
        return None, None
    text = str(raw).strip()
    if _is_placeholder(text):
        inner = _unquote(text)[1:-1].strip(" .\"'").lower()
        return None, (text[:60] if inner in STANCES or inner in STANCE_ALIASES else None)
    word = text.lower().strip(" .\"'")
    word = STANCE_ALIASES.get(word, word)
    if word in STANCES:
        return word, None
    return None, (text[:60] or None)


NEWS_VIEWS = {"bullish", "neutral", "bearish"}
NEWS_VIEW_ALIASES = {"positive": "bullish", "negative": "bearish", "mixed": "neutral"}


def _news_view(raw: Any) -> tuple[str | None, str | None]:
    """(news_view, unreadable text). `negative` / `positive` / `mixed` are
    read as bearish / bullish / neutral; any other word is reported, because
    dropping it left an older note's news view on show as if it were this
    note's."""
    if raw in (None, ""):
        return None, None
    text = str(raw).strip()
    word = text.lower().strip(" .\"'")
    word = NEWS_VIEW_ALIASES.get(word, word)
    if word in NEWS_VIEWS:
        return word, None
    return None, (text[:60] or None)


def _created_ts(created: Any, path: str) -> str:
    """`created_ts` as text. A date/datetime is written ISO; a list (YAML
    `created_ts: [..]`) gives its first item; anything that is not text or a
    date falls back to the file's mtime, the honest default when the key is
    absent."""
    if isinstance(created, (list, tuple)):
        created = next((x for x in created if x not in (None, "")), None)
    if isinstance(created, (dt.datetime, dt.date)):
        return created.isoformat()
    if isinstance(created, str) and created.strip():
        return created.strip()
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")


def _tags(raw: Any) -> list[str]:
    """`tags` as a list of strings whatever YAML made of it: a comma string
    (`tags: earnings, technical`), a mapping, a bare scalar."""
    if raw in (None, "", False):
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    elif isinstance(raw, dict):
        raw = [{k: v} for k, v in raw.items()]
    elif not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [t for t in (_item_text(x) for x in raw) if t and not _is_placeholder(t)]


def parse_discussion_file(path: str) -> dict[str, Any]:
    """Read + validate one markdown file → dict shaped like the DB row.
    Every failure is a ParseError — also the ones nobody anticipated — so
    the caller's choice is always ingest or quarantine."""
    try:
        return _parse_file(path)
    except ParseError:
        raise
    except Exception as e:  # noqa: BLE001 — an odd YAML shape must not escape
        raise ParseError(f"could not read the file: {type(e).__name__}: {e}")


def _parse_file(path: str) -> dict[str, Any]:
    salvaged = False
    try:
        with open(path, encoding="utf-8-sig") as fh:      # -sig drops a UTF-8 BOM
            text = _clean_text(fh.read())
    except (OSError, UnicodeDecodeError) as e:
        raise ParseError(f"frontmatter parse failed: {e}")
    try:
        post = frontmatter.loads(text)
        meta, content = post.metadata or {}, post.content
    except Exception as e:  # YAML errors, half-written files
        got = _salvage_frontmatter(text)
        if not got:
            raise ParseError(f"frontmatter parse failed: {e}")
        meta, content = got
        salvaged = True
    parsed_any = bool(meta)
    raw_stance = meta.get("stance")           # read before placeholders are dropped
    meta = _drop_placeholders(meta)

    body = (content or "").strip()
    if body.split("\n", 1)[0].strip() == "---":
        # The body still starts with '---'. Either the frontmatter never
        # closed (nothing was parsed) or a second block follows the first:
        # storing that would put raw YAML on the timeline as the analysis.
        # A stray extra delimiter in front of real prose is just dropped.
        rest = body.split("\n", 1)[1].strip() if "\n" in body else ""
        if not parsed_any or _KEY_LINE.match(rest):
            raise ParseError("frontmatter not closed — the body still starts with '---'")
        body = rest
    if not body:
        raise ParseError("empty body")
    if _BARE_FENCE.match(body.split("\n", 1)[0].strip()):
        # the reply was saved in pieces: the frontmatter in one code block,
        # chat, then the analysis in a second block. The first block's closing
        # fence is now the top of the body, with the chat and the second
        # block's opening fence under it — residue, not the analysis.
        raise ParseError("the body starts with a bare code fence — the note was saved "
                         "from more than one code block; save the file's contents as "
                         "one piece")
    if _FENCED_FILE.search(body):
        # the whole note is still inside a code fence in the body (chat text
        # around it that _clean_text could not strip): its frontmatter was
        # never read, so ingesting it would file raw YAML as the analysis
        raise ParseError("the body holds a '---' / 'ticker:' frontmatter block — the note "
                         "was saved with chat text around it; save only the file's contents")

    # YAML reads an unquoted ON / NO / YES / TRUE as a boolean; the real
    # ticker is then only in the folder name
    raw_ticker = meta.get("ticker")
    # "$nvda" is how tickers are written in chat: the leading "$" is dropped
    ticker = ("" if isinstance(raw_ticker, bool)
              else str(raw_ticker or "").strip().lstrip("$").upper().strip())
    if not ticker:
        # fall back to the folder convention discussions/{TICKER}/file.md
        ticker = os.path.basename(os.path.dirname(path)).lstrip("$").upper()
    if not ticker or ticker.startswith("_"):
        raise ParseError("no ticker in frontmatter or folder name")

    created_ts = _created_ts(meta.get("created_ts"), path)

    summary = _item_text(meta.get("summary") or "")
    if not summary:
        # first non-heading body line, trimmed for the timeline
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                summary = line[:160]
                break

    tags = _tags(meta.get("tags"))

    # Claude-supplied news assessment (the session's own news check). A word
    # the app cannot read is reported under research_meta.unreadable.
    news_view, news_view_unreadable = _news_view(meta.get("news_view"))

    # Optional decision journal entry (P3). The price at decision time is
    # captured at ingest (upsert_discussion), not here — parsing stays pure.
    decision = str(meta.get("decision") or "").lower().strip() or None
    if decision not in {"buy", "sell", "trim", "hold", "pass", None}:
        decision = None

    # Optional research STANCE — Claude's reasoned call from the session
    # (distinct from `decision`, which is what user decided).
    stance, stance_unreadable = _stance(raw_stance)

    research_meta = _research_meta(meta)
    if stance_unreadable:
        research_meta.setdefault("unreadable", {})["stance"] = stance_unreadable
    stance_note = str(meta.get("stance_note")).strip() if meta.get("stance_note") else None
    if stance is None and not stance_unreadable and _is_v6_meta(research_meta):
        # prompt v6 writes `verdict`, not `stance`: the legacy column is derived
        # so the scorecard, sorting and every older code path keep working
        # (docs/VERDICT_SPEC.md §7). A note from before v6 is left alone.
        stance = VERDICT_TO_STANCE.get(research_meta.get("verdict"))
        if stance:
            research_meta["stance_derived"] = True
            stance_note = stance_note or research_meta.get("verdict_note")
    if news_view_unreadable:
        research_meta.setdefault("unreadable", {})["news_view"] = news_view_unreadable
    if salvaged:
        research_meta["parse_note"] = "frontmatter was not strict YAML; recovered line by line"

    prompt = meta.get("prompt")
    return {
        "research_meta": json.dumps(research_meta) if research_meta else None,
        "ticker": ticker,
        "file_path": os.path.abspath(path),
        "prompt": prompt if prompt is None or isinstance(prompt, str) else _item_text(prompt),
        "response_markdown": body,
        "summary": summary,
        "tags": json.dumps(tags),
        "context_hash": str(meta.get("context_hash") or "") or None,
        "context_snapshot": None,  # optional cache; not populated by files
        "news_view": news_view,
        "news_note": (_item_text(meta.get("news_note")) if meta.get("news_note") else None),
        "decision": decision,
        "stance": stance,
        "stance_horizon": (str(meta.get("stance_horizon")).strip()
                           if meta.get("stance_horizon") else None),
        "stance_note": stance_note,
        "created_ts": created_ts,
        "file_mtime": dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
    }


# prompt v5 keys (all optional; older files simply lack them); v6 adds the
# verdict keys (docs/VERDICT_SPEC.md §7)
_LIST_KEYS = ("upgrade_if", "downgrade_if", "plan_triggers_met",
              "plan_triggers_unknown", "supersedes", "proposed_triggers",
              "hard_flags", "soft_signals", "watch_items")
_TEXT_KEYS = ("value_basis", "verdict_note", "owner_note")
VERDICTS = ("attractive", "fair", "stretched", "avoid", "unrated")
VALUE_GRADES = ("cheap", "fair", "expensive", "paused")
RISK_GRADES = ("clean", "watch", "red_flag")
_ENUMS = {"research_status": {"complete", "partial", "failed"},
          "move_cause": {"found", "not_found", "na"},
          "owned": {"yes", "no"},
          "stance_confidence": {"low", "medium", "high"},
          "verdict": set(VERDICTS),
          "value_grade": set(VALUE_GRADES),
          "risk_grade": set(RISK_GRADES)}
# other spellings of an allowed value; anything else is unreadable (fails closed)
_ENUM_ALIASES = {"research_status": {"incomplete": "partial"},
                 "move_cause": {"n/a": "na"},
                 "value_grade": {"value_paused": "paused"},
                 "risk_grade": {"redflag": "red_flag", "red": "red_flag"}}
# the legacy stance a v6 verdict stands for (scorecard, sorting, older code)
# — one table, in research_quality (§7)
from analysis.research_quality import VERDICT_TO_STANCE  # noqa: E402
V6_KEYS = ("verdict", "value_grade", "risk_grade")


def _is_v6_meta(rm: dict) -> bool:
    """A note written from prompt v6 or later: `prompt_version` ≥ 6, or it
    carries a verdict key (readable or not)."""
    try:
        if int(rm.get("prompt_version") or 0) >= 6:
            return True
    except (TypeError, ValueError):
        pass
    unread = rm.get("unreadable") if isinstance(rm.get("unreadable"), dict) else {}
    return any(k in rm or k in unread for k in V6_KEYS)
_EXTENDED_HOURS = re.compile(r"after[- ]?hours|pre[- ]?market|post[- ]?market|extended", re.I)
_PRICE = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")


def _item_text(x: Any) -> str:
    """One list item as the text the session wrote. YAML turns
    `- by 2027-03-01: SEC closes inquiry` into a one-pair mapping and an
    unquoted timestamp into a datetime; both go back to their text."""
    if isinstance(x, dict):
        return "; ".join(f"{_item_text(k)}: {_item_text(v)}" if v is not None
                         else f"{_item_text(k)}:" for k, v in x.items())
    if isinstance(x, (dt.datetime, dt.date)):
        return x.isoformat()
    return str(x).strip()


def _as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, dict):                  # a block mapping where a list was meant
        v = [{k: val} for k, val in v.items()]
    if not isinstance(v, (list, tuple)):
        v = str(v).split(";")
    out = [_item_text(x) for x in v if not _is_placeholder(x)]
    return [x for x in out if x and not _is_placeholder(x)]


def _price_used(raw: Any) -> tuple[float | None, bool, bool]:
    """(price, readable, extended-hours wording). A number, or text that
    leads with one — "$130.03 (after hours)" → 130.03; a `$` amount anywhere
    wins. Text that only contains a number somewhere ("Nov 20 close 125.53")
    is not guessed at."""
    if isinstance(raw, bool):
        return None, False, False
    if isinstance(raw, (int, float)):
        return float(raw), True, False
    text = str(raw).strip()
    ext = bool(_EXTENDED_HOURS.search(text))
    m = re.search(r"\$\s*(" + _PRICE.pattern + ")", text)
    num = m.group(1) if m else None
    if num is None:
        m = re.match(r"(?:USD\s*)?(" + _PRICE.pattern + ")", text, re.I)    # "USD 125.53"
        num = m.group(1) if m else None
    try:
        return (float(num.replace(",", "")) if num else None), bool(num), ext
    except ValueError:
        return None, False, ext


def _enum_word(v: Any) -> str:
    """An enum value as written, normalized: case, surrounding quotes and
    dots dropped, spaces and hyphens read as underscores — "Not Found" and
    "not-found" are `not_found`."""
    word = str(v).strip().strip("\"'").strip().lower().strip(".")
    return re.sub(r"[\s-]+", "_", word)


def _research_meta(meta: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    unreadable: dict[str, str] = {}          # key → the text the app could not read
    for k, allowed in _ENUMS.items():
        raw = meta.get(k)
        v = raw
        if isinstance(v, bool):              # YAML reads yes/no as booleans
            v = "yes" if v else "no"
        v = _enum_word(v) if v not in (None, "") else None
        v = _ENUM_ALIASES.get(k, {}).get(v, v)
        if v in allowed:
            out[k] = v
        elif v is not None:
            unreadable[k] = str(raw).strip()[:60]
    for k in _LIST_KEYS:
        vals = _as_list(meta.get(k))
        if vals:
            out[k] = vals
    for k in _TEXT_KEYS:
        v = meta.get(k)
        if isinstance(v, (list, tuple)):
            v = "; ".join(_as_list(v))
        elif v not in (None, "") and not isinstance(v, bool):
            v = _item_text(v)
        if isinstance(v, str) and v.strip() and not _is_placeholder(v):
            out[k] = v.strip()
    if meta.get("news_window"):
        out["news_window"] = str(meta["news_window"]).strip()
    rb = meta.get("review_by")
    if rb not in (None, ""):
        due = parse_date(rb)                 # ISO, "Mar 9, 2031", 03/09/2031 …
        if due:
            out["review_by"] = due.isoformat()
        else:
            unreadable["review_by"] = str(rb).strip()[:60]
    for k in ("prompt_version", "sources_count"):
        raw = meta.get(k)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            out[k] = int(raw)
        except (TypeError, ValueError):
            m = re.match(r"\s*(\d+)", str(raw))          # "7 sources"
            if m:
                out[k] = int(m.group(1))
    pu = meta.get("price_used")
    if pu not in (None, ""):
        price, readable, extended = _price_used(pu)
        if readable:
            out["price_used"] = price
        else:
            unreadable["price_used"] = str(pu).strip()[:60]
        if extended:
            out["price_used_note"] = "extended-hours"
    if unreadable:
        out["unreadable"] = unreadable
    return out


# ─── DB ops (upsert keyed on file_path — Q5b edits update in place) ────────────

_ROW_DEFAULTS = {
    "prompt": None, "context_hash": None, "context_snapshot": None,
    "news_view": None, "news_note": None, "decision": None,
    "decision_price": None, "stance": None, "stance_horizon": None,
    "stance_note": None, "research_meta": None,
}


def _capture_decision_price(ticker: str) -> float | None:
    """P3: the price on record when a decision lands. Cached quote first
    (fresh if the user just ran the analysis — the normal flow), cached
    history's last close as fallback. None if neither — the review view
    shows '—' rather than inventing a fill price."""
    import json as _json
    conn = connect()
    try:
        q = conn.execute("SELECT payload FROM cache WHERE key = ?",
                         (f"quote:{ticker}",)).fetchone()
        if q:
            last = (_json.loads(q["payload"]) or {}).get("last")
            if last:
                return float(last)
        h = conn.execute("SELECT payload FROM cache WHERE key = ?",
                         (f"history:{ticker}",)).fetchone()
        if h:
            bars = _json.loads(h["payload"]) or []
            if bars:
                return float(bars[-1]["close"])
    finally:
        conn.close()
    return None


def upsert_discussion(row: dict[str, Any]) -> int:
    row = {**_ROW_DEFAULTS, **row}
    if row.get("decision") and row.get("decision_price") is None:
        row["decision_price"] = _capture_decision_price(row["ticker"])
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO claude_discussions
               (ticker, file_path, prompt, response_markdown, summary, tags,
                context_hash, context_snapshot, news_view, news_note,
                decision, decision_price, stance, stance_horizon, stance_note,
                research_meta, created_ts, file_mtime)
               VALUES (:ticker, :file_path, :prompt, :response_markdown, :summary,
                       :tags, :context_hash, :context_snapshot, :news_view,
                       :news_note, :decision, :decision_price, :stance,
                       :stance_horizon, :stance_note, :research_meta, :created_ts,
                       :file_mtime)
               ON CONFLICT(file_path) DO UPDATE SET
                 ticker=excluded.ticker, prompt=excluded.prompt,
                 response_markdown=excluded.response_markdown,
                 summary=excluded.summary, tags=excluded.tags,
                 context_hash=excluded.context_hash,
                 news_view=excluded.news_view, news_note=excluded.news_note,
                 decision=excluded.decision,
                 decision_price=COALESCE(claude_discussions.decision_price,
                                         excluded.decision_price),
                 stance=excluded.stance, stance_horizon=excluded.stance_horizon,
                 stance_note=excluded.stance_note,
                 research_meta=excluded.research_meta,
                 created_ts=excluded.created_ts, file_mtime=excluded.file_mtime
            """, row)
        conn.commit()
        got = conn.execute("SELECT id FROM claude_discussions WHERE file_path = ?",
                           (row["file_path"],)).fetchone()
        return int(got["id"]) if got else int(cur.lastrowid)
    finally:
        conn.close()


def delete_discussion(file_path: str) -> dict[str, Any] | None:
    """Q5a: file deleted → row hard-deleted (file is source of truth).
    Returns {id, ticker} of the removed row for the SSE event, else None."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, ticker FROM claude_discussions WHERE file_path = ?",
            (os.path.abspath(file_path),)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM claude_discussions WHERE id = ?", (row["id"],))
        conn.commit()
        return {"id": row["id"], "ticker": row["ticker"]}
    finally:
        conn.close()


def list_discussions(ticker: str | None = None, limit: int = 100) -> list[dict]:
    # Ordered by LAST ACTIVITY (file_mtime), newest first — an edited/appended
    # file surfaces at the top instead of sitting at its original created_ts
    # position (the user's ask #2). created_ts breaks ties.
    conn = connect()
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM claude_discussions WHERE ticker = ? "
                "ORDER BY file_mtime DESC, created_ts DESC LIMIT ?",
                (ticker.strip().lstrip("$").upper(), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM claude_discussions "
                "ORDER BY file_mtime DESC, created_ts DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            try:
                d["research_meta"] = json.loads(d.get("research_meta") or "{}")
            except (TypeError, ValueError):
                d["research_meta"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def dedupe_linked_paths() -> dict[str, int]:
    """One discussion file reachable through two paths is ONE discussion.

    After the TickerLens → TickerBands rename, ~/.tickerlens is a symlink to
    ~/.tickerbands, and the catch-up scan re-ingested every file under the new
    path while the old-path rows stayed — 45 discussions were stored twice
    (doubled discussion lists and stance scorecard, found 11/21). The re-read
    copy also captured its decision price at re-read time, not decision time.

    Rule: group rows by the file's real path; keep the row whose path is under
    the current instance dir (else the newest), and carry over the EARLIEST
    decision_price (the price when the user actually decided). Idempotent.
    """
    import os

    import config
    conn = connect()
    merged = removed = 0
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, file_path, decision_price FROM claude_discussions ORDER BY id")]
        groups: dict[str, list[dict]] = {}
        for r in rows:
            try:
                real = os.path.realpath(r["file_path"])
            except OSError:
                real = r["file_path"]
            groups.setdefault(real, []).append(r)
        for real, grp in groups.items():
            if len(grp) < 2:
                continue
            prefix = config.TICKERBANDS_HOME.rstrip("/") + "/"
            keep = next((g for g in reversed(grp) if g["file_path"].startswith(prefix)),
                        grp[-1])
            first_price = next((g["decision_price"] for g in grp
                                if g["decision_price"] is not None), None)
            if first_price is not None:
                conn.execute("UPDATE claude_discussions SET decision_price = ? WHERE id = ?",
                             (first_price, keep["id"]))
            for g in grp:
                if g["id"] != keep["id"]:
                    conn.execute("DELETE FROM claude_discussions WHERE id = ?", (g["id"],))
                    removed += 1
            merged += 1
        conn.commit()
    finally:
        conn.close()
    return {"merged": merged, "removed": removed}
