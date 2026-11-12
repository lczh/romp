#!/usr/bin/env python3
"""Claude Code's own per-call prices, and its arithmetic for one API call's dollars.

The CLI computes every result's total_cost_usd from these rates, so anything in romp that prices tokens and is read
beside that figure (the spend guard's rate, the analytics' session cost, the spend repair's match against the ledger)
prices with this table and nothing else. Before it, the kernel kept a table of its own (2026-10-08, the spend audit):
it had no Opus 5.5 row, so a family fallback priced Opus 5.5 at Opus 4.8's rates, and it had no fast tier, so the
guard read standard Opus 5.5 at about 1.6 times its price and fast Opus 5.5 at about 0.9 times.

The source is Claude Code's baked-in model catalog (its `pricing_tiers` and each model's `pricing` tier, 2.1.282; the
Fable and Mythos 5.2 rows from 2.1.322, which match Anthropic's published pricing page) and, for fast mode, the
per-model fast rates in its cost code, which are not in the catalog. Read off the binary by hand,
not at run time: the catalog is a minified script literal inside a 230 MB executable and the fast rates are code
constants whose names change every build, so nothing romp could parse there is a contract. The table checks against
the ledger instead: priced by it, a turn's calls (its main transcript's and its subagents') sum to the turn's recorded
dollars, fast and standard Opus 5.5 alike. When Claude Code ships a model, add its row here; the SDK backend says
so when a result bills a model the table lacks, or a model's tokens at dollars its rates cannot reach (cost_bounds).

The arithmetic follows the CLI's: cache writes at the 1-hour rate for the tokens `cache_creation.ephemeral_1h_input_tokens`
names and at the 5-minute rate for the rest; a call whose usage says `speed: "fast"` at the model's fast rates where it
has them (a model without them stays at its standard rates, as the CLI does); `inference_geo: "us"` multiplies the
token cost by 1.1; each web search request is a cent.

stdlib only, loaded by path as `romp_cli_prices` from the kernel, the SDK backend and cli/spend_repair.py."""
import re

# $ per million tokens: (input, output, cache write 5 m, cache write 1 h, cache read)
_T_3_15, _T_5_25, _T_10_50 = (3, 15, 3.75, 6, 0.3), (5, 25, 6.25, 10, 0.5), (10, 50, 12.5, 20, 1)
CLI_PRICES = {
    "claude-3-5-haiku": (0.8, 4, 1, 1.6, 0.08), "claude-haiku-4-5": (1, 5, 1.25, 2, 0.1),
    "claude-3-5-sonnet": _T_3_15, "claude-3-7-sonnet": _T_3_15, "claude-sonnet-4-0": _T_3_15,
    "claude-sonnet-4-5": _T_3_15, "claude-sonnet-4-6": _T_3_15, "claude-sonnet-5": (2, 10, 2.5, 4, 0.2),
    "claude-opus-4-0": (15, 75, 18.75, 30, 1.5), "claude-opus-4-1": (15, 75, 18.75, 30, 1.5),
    "claude-opus-4-5": _T_5_25, "claude-opus-4-6": _T_5_25, "claude-opus-4-7": _T_5_25, "claude-opus-4-8": _T_5_25,
    "claude-opus-5": _T_5_25, "claude-opus-5-5": (4, 20, 5, 8, 0.2),
    "claude-fable-5": _T_10_50, "claude-fable-5-1": (10, 50, 12.5, 20, 0.25), "claude-fable-5-2": (8, 40, 10, 16, 0.2),
    "claude-mythos-5": _T_10_50, "claude-mythos-5-1": (10, 50, 12.5, 20, 0.25), "claude-mythos-5-2": (8, 40, 10, 16, 0.2),
}
# fast mode: twice the standard rates on Opus 5.5, Fable 5.2 and Mythos 5.2, Fable-level rates on Opus 4.8 and 5, six times
# on Opus 4.6 and 4.7
CLI_FAST_PRICES = {"claude-opus-5-5": (8, 40, 10, 16, 0.4), "claude-opus-5": _T_10_50, "claude-opus-4-8": _T_10_50,
                   "claude-opus-4-7": (30, 150, 37.5, 60, 3), "claude-opus-4-6": (30, 150, 37.5, 60, 3),
                   "claude-fable-5-2": (16, 80, 20, 32, 0.4), "claude-mythos-5-2": (16, 80, 20, 32, 0.4)}
WEB_SEARCH_USD = 0.01
US_GEO_MULTIPLIER = 1.1

_DATED = re.compile(r"(.+)-\d{8}")                 # a dated model id: the id, a dash, eight digits


def model_id(model):
    """The table's id for a model id as a transcript or a result names it: exact, or a dated form of it
    (`claude-haiku-4-5-20251001`), with any context-window suffix (`[1m]`) dropped; None for a model the table lacks.
    Only a date may follow a known id: `claude-sonnet-5-5` is not `claude-sonnet-5`, nor `claude-fable-5-3`
    `claude-fable-5` (the review of #170: the first cut took any dash, and priced both silently as their neighbour)."""
    m = str(model or "").replace("[1m]", "").replace("[2m]", "").lower()
    if m in CLI_PRICES:
        return m
    d = _DATED.fullmatch(m)
    return d.group(1) if d and d.group(1) in CLI_PRICES else None


def _row(rates):
    i, o, w5, w1, r = rates
    return {"in": i / 1e6, "out": o / 1e6, "cache_w": w5 / 1e6, "cache_w_1h": w1 / 1e6, "cache_r": r / 1e6}


def price_rows():
    """{model id: row} in $ per token, the shape the kernel's price map holds: in, out, cache_w (5 m), cache_w_1h,
    cache_r, and `fast`, the same shape, for a model with fast rates. Newest model first within each family, so a
    lookup that falls back to "any model of the family" lands on the newest one."""
    out = {}
    for mid in reversed(list(CLI_PRICES)):
        row = _row(CLI_PRICES[mid])
        if mid in CLI_FAST_PRICES:
            row["fast"] = _row(CLI_FAST_PRICES[mid])
        out[mid] = row
    return out


def cost_parts(row, tok_in, tok_out, cache_w, cache_r, cache_w_1h=0, fast=False, geo_us=False, web_searches=0):
    """One call's dollars from a price row ($ per token, price_rows' shape) and the call's counts. The row's `fast` rates
    apply when `fast` and the row has them; `cache_w_1h` of the `cache_w` written tokens take the row's 1-hour rate
    (its 5-minute rate when the row names none, as the CLI does for a model without one)."""
    r = row.get("fast") if fast and isinstance(row.get("fast"), dict) else row
    w5 = float(r.get("cache_w") or 0)
    w1 = r.get("cache_w_1h")
    h = min(max(float(cache_w_1h or 0), 0.0), float(cache_w or 0))
    write = float(cache_w or 0) * w5 if w1 is None or h <= 0 else h * float(w1) + (float(cache_w or 0) - h) * w5
    usd = (float(tok_in or 0) * float(r.get("in") or 0) + float(tok_out or 0) * float(r.get("out") or 0)
           + float(cache_r or 0) * float(r.get("cache_r") or 0) + write)
    if geo_us:
        usd *= US_GEO_MULTIPLIER
    return usd + float(web_searches or 0) * WEB_SEARCH_USD


def usage_extras(usage):
    """(1-hour cache write tokens, fast, US inference, web search requests) of a usage block: what a call's price needs
    beyond its four token counts. All neutral for a usage block that names none of them."""
    u = usage if isinstance(usage, dict) else {}
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    st = u.get("server_tool_use") if isinstance(u.get("server_tool_use"), dict) else {}
    return (int(cc.get("ephemeral_1h_input_tokens") or 0), u.get("speed") == "fast", u.get("inference_geo") == "us",
            int(st.get("web_search_requests") or 0))


def call_usd(row, usage):
    """One call's dollars from a price row and the call's usage block as the transcript records it."""
    u = usage if isinstance(usage, dict) else {}
    w1h, fast, geo_us, web = usage_extras(u)
    return cost_parts(row, u.get("input_tokens"), u.get("output_tokens"), u.get("cache_creation_input_tokens"),
                      u.get("cache_read_input_tokens"), w1h, fast, geo_us, web)


_ROWS = price_rows()


def cost_bounds(model, tok_in, tok_out, cache_r, cache_w, web_searches=0):
    """(lowest, highest) dollars the table's rates can bill a model's token totals at, whatever the calls' mix: lowest
    every call at the standard rates with every cache write held 5 minutes, highest every call at the fast rates where
    the model has them, every write held an hour and US inference; None for a model the table lacks. A per-model total
    Claude Code billed outside these is a rate the table does not have (the SDK backend's drift check)."""
    mid = model_id(model)
    if mid is None:
        return None
    row = _ROWS[mid]
    lo = cost_parts(row, tok_in, tok_out, cache_w, cache_r, 0, False, False, web_searches)
    hi = cost_parts(row, tok_in, tok_out, cache_w, cache_r, cache_w, "fast" in row, True, web_searches)
    return lo, hi


def call_cost(model, usage):
    """One API call's cost as Claude Code computes it, or None for a model the table lacks (never priced by a guess)."""
    mid = model_id(model)
    if mid is None or not isinstance(usage, dict):
        return None
    return call_usd(_ROWS[mid], usage)
