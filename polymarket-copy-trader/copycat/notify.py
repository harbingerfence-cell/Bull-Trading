"""Outbound messages. Configure one channel via env vars; everything is also appended to data/outbox.log."""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

from . import config as C


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def send(text, urgent=False):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    with open(os.path.join(C.DATA_DIR, "outbox.log"), "a") as f:
        f.write("---- %s%s\n%s\n" % (datetime.utcnow().isoformat(), " URGENT" if urgent else "", text))
    print(("[ALERT] " if urgent else "[REPORT] ") + text, flush=True)
    sent = False
    try:
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if tok and chat:
            _post("https://api.telegram.org/bot%s/sendMessage" % tok,
                  urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode())
            sent = True
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook:
            _post(hook, json.dumps({"content": text[:1900]}).encode(), {"Content-Type": "application/json"})
            sent = True
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            _post("https://ntfy.sh/" + topic, text.encode(),
                  {"Title": "COPYCAT alert" if urgent else "COPYCAT", "Priority": "high" if urgent else "default"})
            sent = True
    except Exception as e:  # never let a notifier failure stop the bot
        print("notify failed: %s" % e, flush=True)
    return sent
