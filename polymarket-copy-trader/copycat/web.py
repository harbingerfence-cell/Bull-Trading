"""Read-only status page. Serves /<token>/ (dashboard), /<token>/trades.csv, /<token>/skips.csv.
Reads files from disk only; it cannot change state or place anything."""
import csv
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C

TOKEN = os.environ.get("DASH_TOKEN", "")


def _read_csv(name, last=200):
    p = os.path.join(C.DATA_DIR, name)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))[-last:]


def _page():
    try:
        with open(os.path.join(C.DATA_DIR, "state.json")) as f:
            s = json.load(f)
    except Exception:
        return "<p>No data yet.</p>"
    pos = s.get("positions", [])
    bankroll = s["bankroll_start"] + s["realized_pnl"]
    unreal = sum(p["shares"] * p["mark"] - p["cost"] for p in pos)
    eq = bankroll + unreal
    days = (time.time() - s["started_at"]) / 86400
    status = "PAUSED (drawdown)" if s.get("dd_paused") else "Halted today (daily loss)" if s.get("daily_halt") else "Running"
    e = html.escape
    rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%.3f</td><td>%.3f</td><td>$%.2f</td><td class='%s'>%+.2f</td></tr>" % (
        e(p["trader"]), e(p["title"]), e(p["outcome"]), p["entry_price"], p["mark"], p["cost"],
        "pos" if p["shares"] * p["mark"] >= p["cost"] else "neg", p["shares"] * p["mark"] - p["cost"]) for p in pos) \
        or "<tr><td colspan=7>None</td></tr>"
    trades = _read_csv("trades.csv")[::-1]
    trows = "".join("<tr>" + "".join("<td>%s</td>" % e(str(t.get(k, ""))) for k in
                    ("time_ct", "action", "trader", "market", "side", "our_price", "their_price", "size_usd", "result",
                     "running_pnl")) + "</tr>" for t in trades) or "<tr><td colspan=10>No trades yet</td></tr>"
    skips = _read_csv("skips.csv", 50)[::-1]
    srows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        e(k["time_ct"]), e(k["trader"]), e(k["market"]), e(k["reason"])) for k in skips) or "<tr><td colspan=4>None</td></tr>"
    demoted = ", ".join("%s (%s)" % (e(d["name"]), e(d["reason"])) for d in s.get("demoted", {}).values()) or "none"
    return f"""
<h1>Polymarket Copy Trader <span class=tag>PAPER</span></h1>
<div class=cards>
 <div><b>${eq:,.2f}</b>Equity</div>
 <div><b class={'pos' if eq >= s['bankroll_start'] else 'neg'}>{eq - s['bankroll_start']:+,.2f}</b>Total P&amp;L</div>
 <div><b>{s['realized_pnl']:+,.2f}</b>Realized</div>
 <div><b>{unreal:+,.2f}</b>Unrealized</div>
 <div><b>{len(pos)}</b>Open positions</div>
 <div><b>Day {days:.1f}/14</b>{e(status)}</div>
</div>
<p class=muted>Peak equity ${s['peak_equity']:,.2f}. Demoted traders: {demoted}. Updated every 5 min. Page loaded {time.strftime('%H:%M UTC')}.</p>
<h2>Open positions</h2>
<table><tr><th>Trader</th><th>Market</th><th>Side</th><th>Entry</th><th>Now</th><th>Cost</th><th>P&amp;L</th></tr>{rows}</table>
<h2>Trade log <a href="trades.csv">download CSV</a></h2>
<table><tr><th>Time CT</th><th>Action</th><th>Trader</th><th>Market</th><th>Side</th><th>Our</th><th>Their</th><th>Size</th><th>Result</th><th>Running P&amp;L</th></tr>{trows}</table>
<h2>Recent skips <a href="skips.csv">download CSV</a></h2>
<table><tr><th>Time CT</th><th>Trader</th><th>Market</th><th>Reason</th></tr>{srows}</table>"""


CSS = """body{font:14px/1.4 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;background:#fafafa;color:#111}
h1{font-size:22px}.tag{font-size:12px;background:#333;color:#fff;padding:2px 6px;border-radius:4px;vertical-align:middle}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.cards div{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px;color:#666;font-size:12px}
.cards b{display:block;font-size:20px;color:#111}.pos{color:#0a7d32!important}.neg{color:#c62828!important}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;display:block;overflow-x:auto}
th,td{border-bottom:1px solid #eee;padding:6px;text-align:left;white-space:nowrap}h2{font-size:16px;margin-top:24px}
h2 a{font-size:12px;font-weight:normal}.muted{color:#666;font-size:12px}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.cards div,table{background:#1b1b1b;border-color:#333}
.cards b{color:#eee}th,td{border-color:#2a2a2a}.pos{color:#4caf50!important}.neg{color:#ef5350!important}}"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if not TOKEN or parts[0] != TOKEN:
            self.send_response(404)
            self.end_headers()
            return
        sub = parts[1] if len(parts) > 1 else ""
        if sub in ("trades.csv", "skips.csv"):
            p = os.path.join(C.DATA_DIR, sub)
            body = open(p, "rb").read() if os.path.exists(p) else b""
            ctype = "text/csv"
        elif sub == "":
            if not self.path.split("?")[0].endswith("/"):
                self.send_response(301)
                self.send_header("Location", "/%s/" % TOKEN)
                self.end_headers()
                return
            body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<meta http-equiv=refresh content=300><title>Copy Trader</title><style>%s</style>%s" % (CSS, _page())).encode()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start():
    port = int(os.environ.get("PORT", "0") or 0)
    if not port or not TOKEN:
        return False
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("status page on port %d" % port, flush=True)
    return True
