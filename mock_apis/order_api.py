"""Mock ShopGlobal Order + Catalog API (stdlib only).

Serves the workshop's sample data so the gateway has a realistic upstream:
  GET /api/orders/{order_id}     -> one order (404 if unknown)
  GET /api/products              -> catalog list (optional ?q= substring search)
  GET /api/products/{product_id} -> one product (404 if unknown)
  GET /health                    -> ok

The full order record (incl. customer_email, shipping_address) is returned on purpose:
Kong's response-transformer strips those PII fields, which is part of the demo.

There is deliberately NO "list all orders" endpoint: response-transformer only removes
top-level JSON fields, so a list response would leak every customer's email through Kong.

Run:  python3 mock_apis/order_api.py --port 8081
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent / "data"
ORDERS = {o["order_id"]: o for o in json.loads((ROOT / "orders.json").read_text())["orders"]}
_p = json.loads((ROOT / "products.json").read_text())
PRODUCTS = {p["product_id"]: p for p in _p.get("products", _p)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        path = url.path.rstrip("/")
        if path == "/health":
            return self._send(200, {"status": "ok"})
        if path.startswith("/api/orders/"):
            oid = path.rsplit("/", 1)[-1].upper()
            if oid in ORDERS:
                return self._send(200, ORDERS[oid])
            return self._send(404, {"error": "order_not_found", "order_id": oid})
        if path == "/api/products":
            q = (parse_qs(url.query).get("q") or [""])[0].lower()
            items = [p for p in PRODUCTS.values()
                     if not q or q in (p["name"] + " " + p["category"] + " " + p["description"]).lower()]
            return self._send(200, {"products": items})
        if path.startswith("/api/products/"):
            pid = path.rsplit("/", 1)[-1].upper()
            if pid in PRODUCTS:
                return self._send(200, PRODUCTS[pid])
            return self._send(404, {"error": "product_not_found", "product_id": pid})
        return self._send(404, {"error": "not_found"})

    def log_message(self, fmt, *args):
        print("[mock-api]", fmt % args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args()
    print(f"Mock API on :{a.port} with {len(ORDERS)} orders and {len(PRODUCTS)} products")
    ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()
