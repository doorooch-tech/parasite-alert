#!/usr/bin/env python3
"""Daily Parasite Pool summary to Telegram.

Reads live stats from parasite.space (rendered with a headless browser) and
block history from mempool.space, then tells you what to do with your miners.
Env: TG_TOKEN, TG_CHAT.
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

TOKEN, CHAT = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
BRAIINS = 0.975          # Braiins FPPS payout vs fair (2.5% fee)
FINDER = 1.0 / 3.13      # share of a block's value that is the 1 BTC finder bonus
JOIN_MAX, EXIT_EFFORT = 0.45, 3.0

UNIT = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}


# ---------- helpers ----------
def send(text):
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=20)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "parasite-daily/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def num(s):
    """'303.51T' -> 3.0351e14 ; '90.44 PH/s' -> 9.044e16"""
    m = re.match(r"\s*([\d.,]+)\s*([KMGTPE]?)", s)
    return float(m.group(1).replace(",", "")) * UNIT[m.group(2)] if m else None


def after(text, label, pattern=r"[\d.,]+\s*[KMGTPE]?"):
    i = text.find(label)
    if i < 0:
        return None
    m = re.search(pattern, text[i + len(label):])
    return m.group(0) if m else None


def e1(x):
    """Exponential integral E1(x), x>0."""
    if x < 1:
        s, term = 0.0, 1.0
        for k in range(1, 60):
            term *= -x / k
            s += term / k
        return -0.5772156649 - math.log(x) - s
    # continued fraction (Lentz)
    b, c, d = x + 1, 1e300, 1 / (x + 1)
    h = d
    for i in range(1, 200):
        a = -i * i
        b += 2
        d = 1 / (a * d + b)
        c = b + a / c
        h *= c * d
    return h * math.exp(-x)


def share_value(effort):
    """Expected value of a share submitted at this round effort, vs fair (PROP)."""
    if effort <= 0.001:
        effort = 0.001
    return math.exp(effort) * e1(effort)


# ---------- data ----------
def scrape_parasite():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto("https://parasite.space", wait_until="networkidle", timeout=90000)
        pg.wait_for_function(
            "() => document.body.innerText.includes('PH/s') && !document.body.innerText.includes('Loading block data')",
            timeout=90000)
        time.sleep(3)
        text = pg.inner_text("body")
        b.close()
    return text


def parse(text):
    text = re.sub(r"\s+", " ", text)  # labels may wrap across lines
    d = {}
    d["work"] = num(after(text, "Total Work Since Last Block") or "")
    d["net"] = num(after(text, "Minimum Needed Diff") or "")
    nxt = re.search(r"Minimum Needed Diff\s*[\d.,]+\s*[KMGTPE]?\s*→\s*([\d.,]+\s*[KMGTPE]?)", text)
    d["net_next"] = num(nxt.group(1)) if nxt else None
    d["best"] = after(text, "Highest Diff Since Last Block", r"[\d.,]+\s*[KMGTPE]?\s*\([\d.]+%\)")
    d["avg_find"] = after(text, "Avg to Find Block", r"\d+d\s*\d*h?")
    for k, lab in (("hr_now", "Pool Hashrate"), ("hr1", "1D Avg Hashrate"),
                   ("hr6", "6D Avg Hashrate"), ("hr9", "9D Avg Hashrate")):
        v = after(text, lab, r"[\d.,]+\s*[KMGTPE]H/s")
        d[k] = num(v) if v else None
    return d


def mempool_info():
    blocks = get_json("https://mempool.space/api/v1/mining/pool/parasite/blocks")
    last = max(blocks, key=lambda b: b["height"])
    tip = int(urllib.request.urlopen("https://mempool.space/api/blocks/tip/height", timeout=20).read())
    return last, tip, len(blocks)


# ---------- advice ----------
def advise(effort, d):
    join_ev = FINDER + (1 - FINDER) * share_value(effort)
    lines = [f"Joining now ≈ {join_ev*100:.0f}% of fair value until the next block (Braiins = 97.5%)."]
    shrinking = d.get("hr6") and d.get("hr9") and d["hr6"] < 0.5 * d["hr9"]

    if effort > EXIT_EFFORT or shrinking:
        why = "effort above 300%" if effort > EXIT_EFFORT else "6-day hashrate fell below half the 9-day average"
        lines.append(f"🔴 EXIT signal ({why}). Move Parasite miners back to Braiins. "
                     "Shares you already submitted still pay when the next block lands.")
    elif effort < JOIN_MAX:
        lines.append("🟢 FRESH ROUND. Good time to move miners in or add more — new shares are worth "
                     "at least as much as on Braiins.")
    elif effort < 1.0:
        lines.append("🟡 Round is under one block's worth of work. Miners already in: stay. "
                     "Don't add new ones — wait for the next block.")
    else:
        lines.append("🟠 Long round (unlucky so far). Miners already in: stay — your earlier shares "
                     "keep their claim. Don't add new ones until a block resets the round.")
    return lines


def main():
    if not TOKEN or not CHAT:
        sys.exit("Set TG_TOKEN and TG_CHAT")

    msg = ["📊 PARASITE DAILY"]
    try:
        last, tip, nblocks = mempool_info()
        days = (time.time() - last["timestamp"]) / 86400
        msg.append(f"Last block: {last['height']} ({days:.0f} days / {tip-last['height']:,} blocks ago) · "
                   f"pool total {nblocks} blocks")
    except Exception as e:
        msg.append(f"(mempool.space unavailable: {e})")

    try:
        d = parse(scrape_parasite())
        effort = d["work"] / d["net"]
        msg.append(f"Effort: {effort*100:.0f}%  (work {d['work']/1e12:.1f}T / net {d['net']/1e12:.1f}T)")
        if d.get("net_next") and d["net_next"] != d["net"]:
            ch = (d["net_next"] / d["net"] - 1) * 100
            msg.append(f"Next difficulty: {d['net_next']/1e12:.1f}T ({ch:+.1f}%)")
        hr = [f"{lab} {v/1e15:.0f}" for lab, v in (("now", d["hr_now"]), ("1D", d["hr1"]),
                                                   ("6D", d["hr6"]), ("9D", d["hr9"])) if v]
        if hr:
            msg.append("Hashrate PH/s: " + " · ".join(hr))
        if d.get("avg_find"):
            msg.append(f"Expected time per block: {d['avg_find']}")
        if d.get("best"):
            msg.append(f"Best share this round: {d['best']}")
        msg.append("")
        msg += advise(effort, d)
    except Exception as e:
        msg.append(f"\n⚠️ Couldn't read parasite.space ({type(e).__name__}). Check manually: https://parasite.space")

    msg.append("\nhttps://parasite.space")
    send("\n".join(msg))
    print("\n".join(msg))


if __name__ == "__main__":
    main()
