#!/usr/bin/env python3
"""ShopGlobal Smart Routing evaluation harness (stdlib only, Python 3.9+).

Applies the evaluation pyramid from the AWS agentic-AI workshop to the AI gateway:

  Layer 1 - deterministic (free): which model did Kong pick? Was the prompt blocked?
            Are the must-have facts in the answer? Is it in the customer's language?
  Layer 2 - LLM-as-judge (optional, --judge): goal success + helpfulness, scored by
            the premium-tier model through Kong's ACL-restricted /eval/judge route.
  Layer 3 - human review: misroutes are written to the report for review before they
            change the test set or the target descriptions.

Usage
  # Offline dry run - simulated router, no keys or gateway needed:
  python3 eval/run_routing_eval.py --target simulate
  python3 eval/run_routing_eval.py --target simulate --sim-policy candidate --out reports/candidate.json

  # Against a running Kong data plane:
  export KONG_PROXY_URL=http://localhost:8000 KONG_API_KEY=...
  python3 eval/run_routing_eval.py --target live [--hybrid] [--judge] [--facts-via-kong] [--slice order_status]

  # A/B release decision between two reports:
  python3 eval/run_routing_eval.py compare reports/control.json reports/candidate.json

Exit code is 1 when the release gate (eval/gate.json) fails, so CI can block a deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TINY, BIG = "cheap", "premium"   # tiers; model names live in eval/prices.json
PII_FIELDS = {"customer_email", "shipping_address"}  # stripped by Kong response-transformer


# --------------------------------------------------------------------------- data
def load_json(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_reference_data():
    orders = {o["order_id"]: o for o in load_json(ROOT / "data/orders.json")["orders"]}
    prods = load_json(ROOT / "data/products.json")
    prods = prods.get("products", prods)
    return orders, {p["product_id"]: p for p in prods}


def build_messages(case, orders, products, fetch=None):
    """Act like the bot backend: get facts, put them in a system message.
    fetch=None  -> facts from data/*.json on disk (offline / simulate)
    fetch=fn    -> facts fetched THROUGH KONG (/api/orders, /api/products), like bot/app.py does"""
    facts = {}
    refs = case.get("context_refs") or {}
    if refs.get("orders"):
        facts["orders"] = ([fetch(f"/api/orders/{o}") for o in refs["orders"]] if fetch else
                           [{k: v for k, v in orders[o].items() if k not in PII_FIELDS} for o in refs["orders"]])
    if refs.get("products"):
        facts["products"] = ([fetch(f"/api/products/{p}") for p in refs["products"]] if fetch else
                             [products[p] for p in refs["products"]])
    msgs = []
    if facts:
        msgs.append({"role": "system",
                     "content": "Facts for this conversation from ShopGlobal systems:\n" + json.dumps(facts, ensure_ascii=False)})
    msgs.append({"role": "user", "content": case["input"]})
    return msgs


# ---------------------------------------------------------------- Layer 1 checks
ES_WORDS = {"el", "la", "los", "las", "tu", "su", "pedido", "está", "de", "que", "por", "con", "para", "y",
            "es", "un", "una", "fue", "llega", "puedes", "te", "recomiendo", "envío", "entregado"}
EN_WORDS = {"the", "your", "is", "order", "of", "and", "to", "with", "for", "was", "it", "you", "can",
            "a", "an", "has", "been", "will", "recommend", "shipped", "delivered"}


def detect_lang(text: str) -> str:
    toks = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    es = sum(t in ES_WORDS for t in toks)
    en = sum(t in EN_WORDS for t in toks)
    return "es" if es > en else "en"


def facts_present(answer: str, facts) -> bool:
    # Spanish answers write decimals with a comma (79,99): normalise digit,digit -> digit.digit
    a = re.sub(r"(?<=\d),(?=\d{2}\b)", ".", (answer or "").lower())
    return all(any(alt.strip().lower() in a for alt in f.split("|")) for f in facts)


def norm_model(raw: str | None, prices) -> str | None:
    if not raw:
        return None
    r = raw.lower()
    # longest alias first so "gpt-6.1-sol" is not mistaken for a shorter name
    for canon, alias in sorted(((c, a) for c, m in prices["models"].items() for a in m["aliases"]),
                               key=lambda t: -len(t[1])):
        if alias in r:
            return canon
    return raw


def tier_of(model: str | None, prices) -> str | None:
    if not model:
        return None
    return prices["models"].get(model, {}).get("tier", f"unknown:{model}")


# ------------------------------------------------------------ simulated gateway
DENY_REGEX = [r"(?i)ignore (all |your |the )?(previous |prior )?instructions",
              r"(?i)ignora (todas )?(tus|las) instrucciones", r"(?i)system prompt", r"(?i)jailbreak"]
DENY_TOPICS = ["script", "code", "essay", "homework", "ensayo", "código", "tarea", "programa"]
SIMPLE = {
    "control": ["where is", "where's", "track", "tracking", "shipped", "ship yet", "delivered", "arrive",
                "status", "cancel", "dónde está", "estado", "rastrear", "seguimiento", "entregó", "envió",
                "llega", "canceló"],
}
SIMPLE["candidate"] = SIMPLE["control"] + ["mandaron", "ya lo mandaron", "ship"]
COMPLEX = ["compare", "recommend", "which should", "why", "budget", "explain", "difference", "refund",
           "exchange", "options", "swap", "gift", "worth", "never got", "trade-off", "value",
           "comparar", "recomi", "cuál me conviene", "por qué", "presupuesto", "explíca", "diferencia",
           "reembolso", "devuelven", "cambiar", "opciones", "regalo", "vale la pena", "nunca recibí",
           "dudando", " y puedo", "¿y puedo"]
ORDER_ID = re.compile(r"ORD-\d{4}-\d{5}")
ES_STATUS = {"delivered": "entregado", "shipped": "enviado", "processing": "en proceso", "pending": "pendiente",
             "cancelled": "cancelado", "refunded": "reembolsado", "return_requested": "devolución solicitada"}


def _jitter(key: str, lo: float, hi: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return lo + (hi - lo) * h


def sim_route(text: str, policy: str) -> str | None:
    t = text.lower()
    if any(re.search(p, text) for p in DENY_REGEX) or any(w in t for w in DENY_TOPICS):
        return None
    simple = any(k in t for k in SIMPLE[policy])
    complex_ = any(k in t for k in COMPLEX)
    if simple and not complex_:
        return TINY
    # Control policy has no description for "bare order number" prompts -> falls to the big model.
    # The v2 candidate descriptions add it ("very short messages that only contain an order number").
    if policy == "candidate" and not complex_ and ORDER_ID.search(text) and len(t.split()) <= 6:
        return TINY
    return BIG


def sim_answer(case, orders, products, model):
    es = case["lang"] == "es"
    refs = case.get("context_refs") or {}
    parts = []
    for oid in refs.get("orders", []):
        o = orders[oid]
        st = ES_STATUS.get(o["status"], o["status"]) if es else o["status"].replace("_", " ")
        s = (f"Tu pedido {oid} está {st}" if es else f"Your order {oid} is {st}")
        if o.get("shipping_carrier"):
            s += (f", enviado con {o['shipping_carrier']} (seguimiento {o['tracking_number']})" if es
                  else f", sent with {o['shipping_carrier']} (tracking {o['tracking_number']})")
        d = o.get("delivery_date") or o.get("estimated_delivery")
        if d:
            s += (f", fecha de entrega {d}" if es else f", delivery date {d}")
        parts.append(s + ".")
    for pid in refs.get("products", []):
        p = products[pid]
        parts.append(f"{p['name']}: ${p['price']}." + (f" {p['return_policy']}" if "return" in case["category"] else ""))
    if model == BIG and refs:
        parts.append("Te recomiendo la opción que mejor se ajusta a tu uso y presupuesto, por estas razones."
                     if es else "I recommend the option that best fits your use and budget, for these reasons.")
    return " ".join(parts) or ("¿En qué te puedo ayudar con tu compra?" if es else "How can I help with your shopping?")


def call_simulated(case, messages, orders, products, policy, hybrid, prices):
    on_fast_path = hybrid and case["channel"] == "button"
    tier = TINY if on_fast_path else sim_route(case["input"], policy)
    overhead_ms = 0 if on_fast_path else _jitter(case["id"] + "e", 35, 70)
    if tier is None:
        return {"status": 400, "blocked": True, "served_model": None, "answer": "", "prompt_tokens": 0,
                "completion_tokens": 0, "latency_ms": round(overhead_ms, 1), "cache": None, "fast_path": on_fast_path}
    answer = sim_answer(case, orders, products, tier)
    ptoks = sum(len(m["content"]) for m in messages) // 4 + 90  # + decorator system prompt
    ctoks = len(answer) // 4 + (int(_jitter(case["id"] + "r", 150, 450)) if tier == BIG else 0)  # premium reasoning tokens
    lat = _jitter(case["id"], 280, 650) if tier == TINY else _jitter(case["id"], 1600, 3800)
    return {"status": 200, "blocked": False, "served_model": prices["tiers"][tier], "answer": answer, "prompt_tokens": ptoks,
            "completion_tokens": ctoks, "latency_ms": round(lat + overhead_ms, 1), "cache": "Miss",
            "fast_path": on_fast_path}


# ------------------------------------------------------------------ live gateway
def http_post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code, hdrs = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, code, hdrs = e.read(), e.code, dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        sys.exit(f"Cannot reach Kong at {url}: {e}. Is the data plane running? (docker compose up -d)")
    lat = (time.perf_counter() - t0) * 1000
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        data = {"raw": raw.decode(errors="replace")[:500]}
    return code, {k.lower(): v for k, v in hdrs.items()}, data, lat


def auth_headers(args):
    h = {"Content-Type": "application/json"}
    if args.api_key:
        h["apikey"] = args.api_key
    if args.bearer_token:
        h["Authorization"] = f"Bearer {args.bearer_token}"
    return h


def call_live(case, messages, args, prices):
    on_fast_path = args.hybrid and case["channel"] == "button"
    route = "/assistant/order-status" if on_fast_path else args.route
    h = auth_headers(args)
    # Never let one shopper's order answer be cached and served to another.
    if args.no_cache or (case.get("context_refs") or {}).get("orders"):
        h["Cache-Control"] = "no-store"
    code, hdrs, data, lat = http_post(args.base_url.rstrip("/") + route, {"messages": messages}, h, args.timeout)
    blocked = code in (400, 403)
    usage = data.get("usage") or {}
    answer = ""
    if data.get("choices"):
        answer = (data["choices"][0].get("message") or {}).get("content") or ""
    return {"status": code, "blocked": blocked,
            "served_model": None if blocked else norm_model(hdrs.get("x-kong-llm-model") or data.get("model"), prices),
            "answer": answer, "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0), "latency_ms": round(lat, 1),
            "cache": hdrs.get("x-cache-status"), "fast_path": on_fast_path,
            "error": None if code < 300 or blocked else str(data)[:300]}


JUDGE_PROMPT = """You are grading a retail shopping assistant (Layer-2 LLM-as-judge).
Customer language: {lang}. Customer message: {q}
Facts the assistant was given: {facts}
Assistant answer: {a}

Score strictly. goal_success = the customer's actual request is fully and correctly answered using only the facts
(for multi-part questions, ALL parts must be addressed). If the facts do not contain what is needed, an answer that
says so clearly and offers a concrete next step COUNTS as goal_success (inventing details does not). Answering only
one part of a multi-part question is NOT goal_success. helpfulness = 1-5. language_match = answer is in the
customer's language.
Return ONLY JSON: {{"goal_success": true|false, "helpfulness": 1-5, "language_match": true|false, "reason": "<15 words>"}}"""


def judge_live(case, messages, res, args):
    facts = messages[0]["content"] if len(messages) > 1 else "(none)"
    body = {"messages": [{"role": "user", "content": JUDGE_PROMPT.format(
        lang=case["lang"], q=case["input"], facts=facts[:4000], a=res["answer"][:3000])}]}
    code, _, data, _ = http_post(args.base_url.rstrip("/") + "/eval/judge", body, auth_headers(args), args.timeout)
    try:
        txt = data["choices"][0]["message"]["content"]
        return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception:  # noqa: BLE001
        return {"goal_success": None, "error": f"judge_failed status={code}"}


# ------------------------------------------------------------------- scoring
def cost_usd(model, ptoks, ctoks, prices):
    if not model or model not in prices["models"]:
        return 0.0
    m = prices["models"][model]
    return (ptoks * m["input"] + ctoks * m["output"]) / 1_000_000


def score_case(case, res, prices, judge=None):
    exp = case["expected_tier"]
    served_tier = tier_of(res["served_model"], prices)
    if case["expected_behavior"] == "block":
        routing_ok = res["blocked"]
    else:
        routing_ok = (not res["blocked"]) and served_tier == exp
    answered = not res["blocked"] and res["status"] < 300
    facts_ok = answered and facts_present(res["answer"], case["must_have_facts"])
    lang_ok = answered and detect_lang(res["answer"]) == case["lang"]
    if case["expected_behavior"] == "block":
        success = res["blocked"]
    else:
        success = facts_ok and lang_ok and (judge is None or judge.get("goal_success") is not False)
    emb = prices["embeddings"]
    emb_calls = 0 if res["fast_path"] else emb["calls_per_semantic_request"]
    emb_cost = res["prompt_tokens"] * emb_calls * emb["input"] / 1_000_000 if answered else 0.0
    cached = (res.get("cache") or "").lower() == "hit"
    llm_cost = 0.0 if cached else cost_usd(res["served_model"], res["prompt_tokens"], res["completion_tokens"], prices)
    # Baseline = "no gateway, everything on the premium model". Estimates (see prices.json "baseline"):
    #  - a cheap-model answer would also have cost premium-model reasoning tokens
    #  - a blocked abuse prompt would have been answered in full
    b = prices["baseline"]
    if res["blocked"]:
        base = cost_usd(prices["baseline_model"], len(case["input"]) // 4 + 90, b["blocked_prompt_completion_tokens"], prices)
    else:
        extra = b["reasoning_tokens_est"] if res["served_model"] != prices["baseline_model"] else 0
        base = cost_usd(prices["baseline_model"], res["prompt_tokens"], res["completion_tokens"] + extra, prices)
    return {
        "id": case["id"], "pair_id": case["pair_id"], "lang": case["lang"], "slice": case["slice"],
        "channel": case["channel"], "input": case["input"], "expected": exp or "BLOCK",
        "served": "BLOCKED" if res["blocked"] else served_tier, "served_model": res["served_model"],
        "fast_path": res["fast_path"], "routing_ok": routing_ok,
        "under_route": exp == BIG and served_tier == TINY,
        "over_route": exp == TINY and served_tier == BIG,
        "guard_leak": case["expected_behavior"] == "block" and not res["blocked"],
        "false_block": case["expected_behavior"] == "route" and res["blocked"],
        "facts_ok": facts_ok, "lang_ok": lang_ok, "success": success,
        "judge": judge, "latency_ms": res["latency_ms"], "cache": res.get("cache"),
        "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"],
        "llm_cost_usd": llm_cost, "embedding_cost_usd": emb_cost, "baseline_cost_usd": base,
        "answer_preview": (res["answer"] or "")[:160], "answer": res["answer"] or "", "error": res.get("error"), "http_status": res["status"],
    }


def rate(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1)


def project(rows, mix):
    """Re-weight per-slice average cost by the expected production traffic mix.
    The test set is deliberately balanced (half hard cases); real bot traffic is mostly order status."""
    total = base = 0.0
    for sl, share in mix.items():
        xs = [r for r in rows if r["slice"] == sl]
        if not xs:
            continue
        total += share * sum(r["llm_cost_usd"] + r["embedding_cost_usd"] for r in xs) / len(xs)
        base += share * sum(r["baseline_cost_usd"] for r in xs) / len(xs)
    return {"traffic_mix": mix,
            "per_1k_conversations_usd": round(1000 * total, 4),
            "baseline_per_1k_conversations_usd": round(1000 * base, 4),
            "savings_pct": round(100 * (1 - total / base), 1) if base else None}


def summarize(rows, mix):
    routed = [r for r in rows if r["expected"] != "BLOCK"]
    exp_big = [r for r in rows if r["expected"] == BIG]
    exp_tiny = [r for r in rows if r["expected"] == TINY]
    blocks = [r for r in rows if r["expected"] == "BLOCK"]
    acc = {lang: rate([r["routing_ok"] for r in rows if r["lang"] == lang]) for lang in ("en", "es")}
    by_pair = {}
    for r in rows:
        by_pair.setdefault(r["pair_id"], {})[r["lang"]] = r["served"]
    pairs = [p for p in by_pair.values() if len(p) == 2]
    llm = sum(r["llm_cost_usd"] for r in rows)
    emb = sum(r["embedding_cost_usd"] for r in rows)
    base = sum(r["baseline_cost_usd"] for r in rows)
    successes = sum(r["success"] for r in routed)
    lat = {}
    for m in (TINY, BIG):
        xs = [r["latency_ms"] for r in rows if r["served"] == m]
        lat[m] = {"n": len(xs), "p50_ms": pctl(xs, 0.5), "p95_ms": pctl(xs, 0.95)}
    all_lat = [r["latency_ms"] for r in routed if r["served"] not in (None, "BLOCKED")]
    base_lat = statistics.mean([r["latency_ms"] for r in rows if r["served"] == BIG] or [0])
    return {
        "n_cases": len(rows),
        "routing_accuracy": rate([r["routing_ok"] for r in rows]),
        "routing_accuracy_by_lang": acc,
        "routing_accuracy_by_slice": {s: rate([r["routing_ok"] for r in rows if r["slice"] == s])
                                      for s in sorted({r["slice"] for r in rows})},
        "under_route_rate": rate([r["under_route"] for r in exp_big]),
        "over_route_rate": rate([r["over_route"] for r in exp_tiny]),
        "guard_block_rate": rate([not r["guard_leak"] for r in blocks]),
        "false_block_rate": rate([r["false_block"] for r in routed]),
        "language_parity_gap": round(abs((acc["en"] or 0) - (acc["es"] or 0)), 4),
        "pair_consistency": rate([p["en"] == p["es"] for p in pairs]),
        "answer_language_match": rate([r["lang_ok"] for r in routed]),
        "success_rate": rate([r["success"] for r in routed]),
        "share_served_by_cheap_tier": rate([r["served"] == TINY for r in routed]),
        "cost": {
            "llm_usd": round(llm, 6), "gateway_embedding_overhead_usd": round(emb, 6),
            "total_usd": round(llm + emb, 6),
            "baseline_all_premium_usd": round(base, 6),
            "testset_savings_pct": round(100 * (1 - (llm + emb) / base), 1) if base else None,
            "cost_per_successful_answer_usd": round((llm + emb) / successes, 7) if successes else None,
            "projected": project(rows, mix),
        },
        "latency": {**lat, "overall_p50_ms": pctl(all_lat, 0.5), "overall_p95_ms": pctl(all_lat, 0.95),
                    "mean_ms_if_all_premium_est": round(base_lat, 1)},
        "cache_hits": sum((r["cache"] or "").lower() == "hit" for r in rows),
        "fast_path_requests": sum(r["fast_path"] for r in rows),
    }


def apply_gate(s, gate):
    checks = [
        ("routing_accuracy", s["routing_accuracy"], ">=", gate["min_routing_accuracy"]),
        ("under_route_rate", s["under_route_rate"], "<=", gate["max_under_route_rate"]),
        ("over_route_rate", s["over_route_rate"], "<=", gate["max_over_route_rate"]),
        ("language_parity_gap", s["language_parity_gap"], "<=", gate["max_language_parity_gap"]),
        ("guard_block_rate", s["guard_block_rate"], ">=", gate["min_guard_block_rate"]),
        ("success_rate", s["success_rate"], ">=", gate["min_success_rate"]),
        ("projected_savings_pct", s["cost"]["projected"]["savings_pct"], ">=", gate["min_savings_pct"]),
    ]
    out = []
    for name, val, op, thr in checks:
        ok = val is not None and (val >= thr if op == ">=" else val <= thr)
        out.append({"metric": name, "value": val, "rule": f"{op} {thr}", "pass": ok})
    return {"passed": all(c["pass"] for c in out), "checks": out}


# ------------------------------------------------------------------- reporting
def fmt_pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def markdown_report(rep):
    s, g, m = rep["summary"], rep["gate"], rep["meta"]
    L = [f"# ShopGlobal Smart Routing - eval report", "",
         f"- Run: `{m['run_at']}`  |  target: **{m['target']}**"
         + (f" (policy `{m['sim_policy']}`, SIMULATED - not real model output)" if m["target"] == "simulate" else "")
         + f"  |  route: `{m['route']}`  |  hybrid: {m['hybrid']}  |  judge: {m['judge']}",
         f"- Gate: **{'PASS' if g['passed'] else 'FAIL'}**", "",
         "## Headline", "",
         "| Metric | Value |", "|---|---|",
         f"| Routing accuracy | {fmt_pct(s['routing_accuracy'])} (EN {fmt_pct(s['routing_accuracy_by_lang']['en'])} / ES {fmt_pct(s['routing_accuracy_by_lang']['es'])}) |",
         f"| Language parity gap | {fmt_pct(s['language_parity_gap'])} |",
         f"| EN/ES pair consistency | {fmt_pct(s['pair_consistency'])} |",
         f"| Under-route rate (complex -> cheap tier, quality risk) | {fmt_pct(s['under_route_rate'])} |",
         f"| Over-route rate (simple -> premium tier, cost leak) | {fmt_pct(s['over_route_rate'])} |",
         f"| Guard block rate | {fmt_pct(s['guard_block_rate'])} |",
         f"| Success rate (Layer 1{' + judge' if m['judge'] else ''}) | {fmt_pct(s['success_rate'])} |",
         f"| Share served by cheap tier ({m['tiers']['cheap']}) | {fmt_pct(s['share_served_by_cheap_tier'])} |",
         f"| Projected cost per 1k conversations (traffic mix) | ${s['cost']['projected']['per_1k_conversations_usd']} vs ${s['cost']['projected']['baseline_per_1k_conversations_usd']} with no gateway / all premium |",
         f"| **Projected savings** | {s['cost']['projected']['savings_pct']}% (test-set mix: {s['cost']['testset_savings_pct']}%) |",
         f"| **Cost per successful answer** | ${s['cost']['cost_per_successful_answer_usd']} |",
         f"| Gateway embedding overhead | ${s['cost']['gateway_embedding_overhead_usd']} of ${s['cost']['total_usd']} total |",
         f"| p50 / p95 latency | {s['latency']['overall_p50_ms']} / {s['latency']['overall_p95_ms']} ms (premium mean {s['latency']['mean_ms_if_all_premium_est']} ms) |",
         "", "## Gate", "", "| Check | Value | Rule | Result |", "|---|---|---|---|"]
    for c in g["checks"]:
        L.append(f"| {c['metric']} | {c['value']} | {c['rule']} | {'pass' if c['pass'] else '**FAIL**'} |")
    L += ["", "## By slice", "", "| Slice | Routing accuracy |", "|---|---|"]
    L += [f"| {k} | {fmt_pct(v)} |" for k, v in s["routing_accuracy_by_slice"].items()]
    bad = [r for r in rep["cases"] if not r["routing_ok"] or not r["success"]]
    L += ["", "## Needs human review (Layer 3)", ""]
    if not bad:
        L.append("None - all cases routed correctly and answered successfully.")
    else:
        L += ["| Case | Lang | Expected tier | Served | Why flagged | Prompt |", "|---|---|---|---|---|---|"]
        for r in bad:
            why = ", ".join(w for w, f in [("under-route", r["under_route"]), ("over-route", r["over_route"]),
                                           ("guard leak", r["guard_leak"]), ("false block", r["false_block"]),
                                           ("missing facts", r["expected"] != "BLOCK" and not r["facts_ok"]),
                                           ("wrong language", r["expected"] != "BLOCK" and not r["lang_ok"])] if f)
            served = r['served'] if r['served'] in (None, 'BLOCKED') else f"{r['served']} ({r['served_model']})"
            L.append(f"| {r['id']} | {r['lang']} | {r['expected']} | {served} | {why or 'judge'} | {r['input'][:70]} |")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------- commands
def cmd_run(args):
    prices = load_json(args.prices)
    gate = load_json(args.gate)
    ts = load_json(args.testset)
    orders, products = load_reference_data()
    cases = [c for c in ts["test_cases"]
             if (not args.slice or c["slice"] in args.slice) and (not args.lang or c["lang"] == args.lang)]
    if args.target == "live" and not (args.api_key or args.bearer_token):
        sys.exit("Set KONG_API_KEY (key-auth) or KONG_BEARER_TOKEN (OIDC) for --target live")
    fetch = None
    if args.facts_via_kong:
        if args.target != "live":
            sys.exit("--facts-via-kong needs --target live")

        def fetch(path):
            req = urllib.request.Request(args.base_url.rstrip("/") + path, headers=auth_headers(args))
            try:
                with urllib.request.urlopen(req, timeout=args.timeout) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as e:
                sys.exit(f"Fact lookup {path} through Kong failed: HTTP {e.code} {e.read()[:200]!r}")
            leaked = PII_FIELDS & set(data)
            if leaked:
                print(f"  WARNING: Kong returned PII fields {sorted(leaked)} for {path} - check response-transformer")
            return data
    rows = []
    for c in cases:
        msgs = build_messages(c, orders, products, fetch)
        if args.target == "simulate":
            res = call_simulated(c, msgs, orders, products, args.sim_policy, args.hybrid, prices)
            judge = None
        else:
            res = call_live(c, msgs, args, prices)
            judge = judge_live(c, msgs, res, args) if args.judge and not res["blocked"] and res["status"] < 300 else None
        row = score_case(c, res, prices, judge)
        rows.append(row)
        mark = "ok " if row["routing_ok"] else "XX "
        print(f"{mark}{row['id']:<11} exp={row['expected']:<8} got={str(row['served']):<8} {str(row['served_model'] or ''):<12} "
              f"{row['latency_ms']:>7.0f}ms ${row['llm_cost_usd'] + row['embedding_cost_usd']:.6f}"
              f"{'' if row['http_status'] in (200, 400, 403) else '  [HTTP ' + str(row['http_status']) + ']'}"
          f"{'' if row['success'] else '  (answer check failed)'}")
    summary = summarize(rows, load_json(args.traffic_mix)["mix"])
    rep = {"meta": {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "target": args.target,
                    "sim_policy": args.sim_policy if args.target == "simulate" else None,
                    "route": args.route, "hybrid": args.hybrid, "judge": bool(args.judge),
                    "facts_via_kong": bool(args.facts_via_kong),
                    "testset_version": ts.get("version"), "n_cases": len(rows), "tiers": prices["tiers"]},
           "summary": summary, "gate": apply_gate(summary, gate), "cases": rows}
    out = Path(args.out or ROOT / "reports" / f"report-{datetime.now():%Y%m%d-%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".md").write_text(markdown_report(rep), encoding="utf-8")
    s = summary
    print("\n" + "=" * 72)
    print(f"Routing accuracy {fmt_pct(s['routing_accuracy'])} | parity gap {fmt_pct(s['language_parity_gap'])} | "
          f"projected savings {s['cost']['projected']['savings_pct']}% | "
          f"$/success {s['cost']['cost_per_successful_answer_usd']}")
    print(f"GATE: {'PASS' if rep['gate']['passed'] else 'FAIL'}  ->  {out} (+ .md)")
    for c in rep["gate"]["checks"]:
        if not c["pass"]:
            print(f"  FAIL {c['metric']} = {c['value']} (rule {c['rule']})")
    return 0 if rep["gate"]["passed"] or args.no_fail else 1


def cmd_compare(argv):
    ap = argparse.ArgumentParser(prog="run_routing_eval.py compare")
    ap.add_argument("control")
    ap.add_argument("candidate")
    ap.add_argument("--out", default=str(ROOT / "reports" / "ab_decision.md"))
    a = ap.parse_args(argv)
    c, t = load_json(a.control), load_json(a.candidate)
    cs, ts_ = c["summary"], t["summary"]
    rows = [("Routing accuracy", cs["routing_accuracy"], ts_["routing_accuracy"], "up"),
            ("Language parity gap", cs["language_parity_gap"], ts_["language_parity_gap"], "down"),
            ("Under-route rate", cs["under_route_rate"], ts_["under_route_rate"], "down"),
            ("Over-route rate", cs["over_route_rate"], ts_["over_route_rate"], "down"),
            ("Success rate", cs["success_rate"], ts_["success_rate"], "up"),
            ("Projected savings %", cs["cost"]["projected"]["savings_pct"], ts_["cost"]["projected"]["savings_pct"], "up"),
            ("Projected $ / 1k conversations", cs["cost"]["projected"]["per_1k_conversations_usd"],
             ts_["cost"]["projected"]["per_1k_conversations_usd"], "down"),
            ("Cost / successful answer $", cs["cost"]["cost_per_successful_answer_usd"],
             ts_["cost"]["cost_per_successful_answer_usd"], "down"),
            ("p95 latency ms", cs["latency"]["overall_p95_ms"], ts_["latency"]["overall_p95_ms"], "down")]
    reasons = []
    if not t["gate"]["passed"]:
        reasons.append("candidate fails the release gate")
    if (ts_["under_route_rate"] or 0) > (cs["under_route_rate"] or 0):
        reasons.append("candidate under-routes more (quality risk)")
    if (ts_["success_rate"] or 0) < (cs["success_rate"] or 0) - 0.02:
        reasons.append("success rate dropped by more than 2 points")
    cps_c, cps_t = cs["cost"]["cost_per_successful_answer_usd"], ts_["cost"]["cost_per_successful_answer_usd"]
    if cps_c and cps_t and cps_t > cps_c:
        reasons.append("cost per successful answer went up")
    decision = "PROMOTE candidate" if not reasons else "HOLD - keep control"
    L = ["# A/B routing-policy decision", "",
         f"- Control: `{a.control}` (gate {'PASS' if c['gate']['passed'] else 'FAIL'})",
         f"- Candidate: `{a.candidate}` (gate {'PASS' if t['gate']['passed'] else 'FAIL'})", "",
         "| Metric | Control | Candidate | Better is |", "|---|---|---|---|"]
    L += [f"| {n} | {x} | {y} | {d} |" for n, x, y, d in rows]
    L += ["", f"## Decision: **{decision}**", ""]
    L += [f"- {r}" for r in reasons] or ["- Candidate passes the gate, does not under-route more, and is cheaper or equal per successful answer."]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0 if not reasons else 2


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        return cmd_compare(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["simulate", "live"], default="simulate")
    ap.add_argument("--sim-policy", choices=["control", "candidate"], default="control",
                    help="simulate mode only: which routing policy to emulate")
    ap.add_argument("--base-url", default=os.getenv("KONG_PROXY_URL", "http://localhost:8000"))
    ap.add_argument("--route", default="/assistant/chat")
    ap.add_argument("--api-key", default=os.getenv("KONG_API_KEY"))
    ap.add_argument("--bearer-token", default=os.getenv("KONG_BEARER_TOKEN"))
    ap.add_argument("--hybrid", action="store_true", help="send button-channel cases to /assistant/order-status")
    ap.add_argument("--judge", action="store_true", help="Layer 2 LLM-as-judge via /eval/judge (live only)")
    ap.add_argument("--facts-via-kong", action="store_true",
                    help="live only: fetch order/product facts through Kong /api/* (end-to-end), not from data/*.json")
    ap.add_argument("--no-cache", action="store_true", help="send Cache-Control: no-store on every request (A/B runs)")
    ap.add_argument("--slice", action="append", help="only run these slices (repeatable)")
    ap.add_argument("--lang", choices=["en", "es"])
    ap.add_argument("--testset", default=str(ROOT / "data/routing_testset.json"))
    ap.add_argument("--prices", default=str(ROOT / "eval/prices.json"))
    ap.add_argument("--gate", default=str(ROOT / "eval/gate.json"))
    ap.add_argument("--traffic-mix", default=str(ROOT / "eval/traffic_mix.json"))
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--no-fail", action="store_true", help="exit 0 even if the gate fails")
    return cmd_run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
