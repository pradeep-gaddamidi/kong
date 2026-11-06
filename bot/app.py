#!/usr/bin/env python3
"""ShopGlobal shopping-assistant BOT BACKEND (stdlib only, Python 3.9+).

This is the "bot backend" box in the architecture: the server-side app between the
customer's chat window and Kong. It is the ONLY component that holds the Kong API key.

For every customer message it:
  1. finds order numbers in the text and fetches each order THROUGH KONG  (GET /api/orders/{id})
  2. finds products mentioned and fetches them THROUGH KONG               (GET /api/products)
  3. sends the question + those facts to Kong's AI route                   (POST /assistant/chat,
     or /assistant/order-status for the "Check Order Status" button)
  4. returns the answer plus what Kong decided: model, tier, cost, latency, cache, blocked

Endpoints
  GET  /            -> the demo chat page (bot/index.html)
  POST /chat        -> {"message": "...", "channel": "chat"|"button", "history": [...]}
  GET  /health      -> ok

Config (environment, e.g. from .env)
  KONG_PROXY_URL    default http://localhost:8000   (in docker-compose: http://kong-dp:8000)
  DECK_BOT_API_KEY  the shop-bot-web consumer key (same value decK pushed to Kong)
  BOT_PORT          default 8090

Run on your Mac:
  set -a; source .env; set +a
  python3 bot/app.py            # then open http://localhost:8090

DEMO LIMITATION: there is no customer login here, so anyone can ask about any order
number. A production bot must check the order belongs to the signed-in customer
(e.g. Keycloak/OIDC identity) BEFORE fetching it.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KONG = os.getenv("KONG_PROXY_URL", "http://localhost:8000").rstrip("/")
BOT_KEY = os.getenv("DECK_BOT_API_KEY", "")
PORT = int(os.getenv("BOT_PORT", "8090"))
PRICES = json.loads((ROOT / "eval/prices.json").read_text())
INDEX_HTML = (Path(__file__).resolve().parent / "index.html").read_bytes()

ORDER_ID = re.compile(r"\bORD-\d{4}-\d{5}\b", re.I)
# Words customers use for products -> catalog search terms (English + Spanish).
PRODUCT_TERMS = {
    "headphone": "headphones", "auricular": "headphones", "cascos": "headphones",
    "earbud": "earbuds", "audífono": "earbuds", "audifono": "earbuds",
    "watch": "watch", "reloj": "watch", "band": "band", "correa": "band",
    "monitor": "monitor", "pantalla": "monitor", "screen": "monitor",
    "chair": "chair", "silla": "chair", "hub": "hub", "usb": "hub",
    "keyboard": "keyboard", "teclado": "keyboard", "gaming": "gaming", "gamer": "gaming",
    "speaker": "speaker", "altavoz": "speaker", "bocina": "speaker",
    "webcam": "webcam", "cámara": "webcam", "camara": "webcam",
}
# Questions that need the catalog even if no product is named ("recommend a gift under $100").
BROWSE_HINTS = ["recommend", "suggest", "gift", "budget", "under $", "setup", "what do you sell",
                "recomiend", "regalo", "presupuesto", "menos de", "equipo", "qué venden"]


# ---------------------------------------------------------------- Kong calls
def kong(method: str, path: str, body: dict | None = None, extra_headers: dict | None = None):
    """Call Kong as the shop-bot-web consumer. Returns (status, headers, json, latency_ms)."""
    headers = {"apikey": BOT_KEY, "Content-Type": "application/json", **(extra_headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(KONG + path, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, status, hdrs = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read(), e.code, e.headers
    except urllib.error.URLError as e:
        return 502, {}, {"error": f"cannot reach Kong at {KONG}: {e.reason}"}, 0.0
    ms = round((time.perf_counter() - t0) * 1000, 1)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        payload = {"raw": raw.decode(errors="replace")[:300]}
    return status, {k.lower(): v for k, v in (hdrs or {}).items()}, payload, ms


def fetch_orders(message: str) -> list[dict]:
    facts = []
    for oid in dict.fromkeys(m.upper() for m in ORDER_ID.findall(message)):   # unique, in order
        status, _, data, _ = kong("GET", f"/api/orders/{oid}")
        facts.append(data if status == 200 else {"order_id": oid, "lookup_result": "order not found"})
    return facts


def fetch_products(message: str) -> list[dict]:
    text = message.lower()
    terms = list(dict.fromkeys(v for k, v in PRODUCT_TERMS.items() if k in text))
    found: dict[str, dict] = {}
    for term in terms:
        status, _, data, _ = kong("GET", "/api/products?q=" + urllib.parse.quote(term))
        if status == 200:
            for p in data.get("products", []):
                found[p["product_id"]] = p
    if not found and any(h in text for h in BROWSE_HINTS):
        # Small demo catalog: send a compact version of everything (production: search / RAG).
        status, _, data, _ = kong("GET", "/api/products")
        if status == 200:
            for p in data.get("products", []):
                found[p["product_id"]] = {k: p[k] for k in
                                          ("product_id", "name", "category", "price", "rating", "in_stock")}
    return list(found.values())


# ------------------------------------------------------------- cost helpers
def model_info(header_value: str | None, body_model: str | None):
    raw = (header_value or body_model or "").lower()
    for name, m in sorted(PRICES["models"].items(), key=lambda kv: -len(kv[0])):
        if any(a in raw for a in m["aliases"]):
            return name, m
    return (raw or None), None


def cost_usd(m: dict | None, usage: dict) -> float | None:
    if not m:
        return None
    return round((usage.get("prompt_tokens", 0) * m["input"] + usage.get("completion_tokens", 0) * m["output"]) / 1e6, 6)


# --------------------------------------------------------------- main logic
def answer(message: str, channel: str = "chat", history: list | None = None) -> dict:
    orders = fetch_orders(message)
    products = fetch_products(message)

    messages = []
    facts = {k: v for k, v in (("orders", orders), ("products", products)) if v}
    if facts:
        messages.append({"role": "system",
                         "content": "Facts for this conversation from ShopGlobal systems:\n"
                                    + json.dumps(facts, ensure_ascii=False)})
    for turn in (history or [])[-6:]:                       # short memory, user/assistant only
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": message})

    route = "/assistant/order-status" if channel == "button" else "/assistant/chat"
    # Personal order data must never land in the shared semantic cache.
    extra = {"Cache-Control": "no-store"} if orders else {}
    status, hdrs, data, ms = kong("POST", route, {"messages": messages}, extra)

    model, m = model_info(hdrs.get("x-kong-llm-model"), data.get("model"))
    result = {
        "route": route, "status": status, "latency_ms": ms,
        "model": model, "tier": (m or {}).get("tier"),
        "cost_usd": cost_usd(m, data.get("usage") or {}),
        "cache": hdrs.get("x-cache-status"),
        "facts_used": {"orders": [o.get("order_id") for o in orders],
                       "products": [p.get("product_id") for p in products]},
        "blocked": False, "answer": "",
    }
    if status == 200 and data.get("choices"):
        result["answer"] = (data["choices"][0].get("message") or {}).get("content") or ""
    elif status in (400, 403):
        result["blocked"] = True
        result["answer"] = ("I can only help with ShopGlobal orders and products. / "
                            "Solo puedo ayudarte con pedidos y productos de ShopGlobal.")
    elif status == 429:
        result["answer"] = ("We're getting a lot of questions right now, please try again shortly. / "
                            "Estamos recibiendo muchas preguntas, inténtalo de nuevo en un momento.")
        result["retry_after"] = hdrs.get("retry-after")
    else:
        result["answer"] = "Sorry, something went wrong. / Lo sentimos, algo salió mal."
        result["error"] = data
    return result


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        if self.path == "/health":
            return self._send(200, {"status": "ok", "kong": KONG, "bot_key_set": bool(BOT_KEY)})
        return self._send(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/chat":
            return self._send(404, {"error": "not_found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid_json"})
        msg = str(body.get("message", "")).strip()
        if not msg:
            return self._send(400, {"error": "empty_message"})
        result = answer(msg[:2000], body.get("channel", "chat"), body.get("history"))
        print(f"[bot] {result['route']} -> {result['status']} model={result['model']} "
              f"cost=${result['cost_usd']} facts={result['facts_used']}")
        return self._send(200, result)

    def log_message(self, fmt, *args):
        pass  # keep the console readable; answer() prints one line per chat


if __name__ == "__main__":
    if not BOT_KEY:
        print("WARNING: DECK_BOT_API_KEY is not set - Kong will reject requests (401). Run: set -a; source .env; set +a")
    print(f"ShopGlobal bot on http://localhost:{PORT}  ->  Kong {KONG}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
