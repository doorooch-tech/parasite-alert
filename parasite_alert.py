#!/usr/bin/env python3
"""Telegram alert when Parasite Pool finds a BTC block.

Data source: mempool.space tracks Parasite Pool by its coinbase tag (slug "parasite").
State: remembers the last block height it alerted on in last_height.txt.
Env: TG_TOKEN (bot token), TG_CHAT (your chat id).
"""
import json, os, sys, urllib.request, urllib.parse, datetime

API = "https://mempool.space/api/v1/mining/pool/parasite/blocks"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_height.txt")
TOKEN, CHAT = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "parasite-alert/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def send(text):
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=20)


def main():
    if not TOKEN or not CHAT:
        sys.exit("Set TG_TOKEN and TG_CHAT")
    blocks = get_json(API)
    if not blocks:
        return
    newest = max(b["height"] for b in blocks)

    last = None
    if os.path.exists(STATE):
        last = int(open(STATE).read().strip() or 0)

    if "--test" in sys.argv:
        send(f"✅ Parasite alert is working. Latest Parasite block: {newest}")
        return

    if last is None:  # first run: just remember, don't alert on old blocks
        open(STATE, "w").write(str(newest))
        print(f"Initialized at {newest}")
        return

    new = sorted((b for b in blocks if b["height"] > last), key=lambda b: b["height"])
    for b in new:
        ts = datetime.datetime.fromtimestamp(b["timestamp"], datetime.timezone(datetime.timedelta(hours=9)))
        reward = b.get("extras", {}).get("reward")
        reward_txt = f"{reward/1e8:.5f} BTC" if reward else "?"
        send("⛏️ PARASITE FOUND A BLOCK!\n"
             f"Height: {b['height']}\nTime (KST): {ts:%Y-%m-%d %H:%M}\nReward: {reward_txt}\n"
             f"https://mempool.space/block/{b['id']}\n\n"
             "New round started → good time to move miners (Work/Net < ~45%).\n"
             "https://parasite.space")
    if new:
        open(STATE, "w").write(str(newest))
    print(f"last={last} newest={newest} alerted={len(new)}")


if __name__ == "__main__":
    main()
