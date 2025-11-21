import os
import requests
import pandas as pd
import yfinance as yf
from io import BytesIO
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

MIN_LISTING_DAYS = 120
THRESHOLD = 0.01
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")


def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass


def fetch_equity_list():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    r = requests.get(url, timeout=40)
    df = pd.read_csv(BytesIO(r.content))
    df.columns = df.columns.str.strip()
    return df


def get_recent_ipos(days: int):
    df = fetch_equity_list()

    df["DATE OF LISTING"] = pd.to_datetime(df["DATE OF LISTING"], errors="coerce")
    df = df.dropna(subset=["DATE OF LISTING"])

    cutoff = datetime.now() - timedelta(days=days)
    recent = df[df["DATE OF LISTING"] >= cutoff]
    recent = recent[recent["SERIES"] == "EQ"]

    return recent["SYMBOL"].tolist(), recent


def fetch_history(symbol):
    try:
        hist = yf.Ticker(symbol + ".NS").history(period="max")
        return hist if not hist.empty else None
    except:
        return None


# ---------------------------------------------------------
# Exclude today's candle for ATL calculation
# ---------------------------------------------------------
def compute_atl_excluding_today(hist):
    try:
        hist_no_today = hist.iloc[:-1]

        atl = hist_no_today["Low"].min()
        idx = hist_no_today["Low"].idxmin()
        pos = hist_no_today.index.get_loc(idx)

        return atl, idx, pos, len(hist_no_today)
    except:
        return None


# ---------------------------------------------------------
# Filter 1 — Volume Spike Filter (Globally used)
# ---------------------------------------------------------
def volume_spike_filter(hist):
    if len(hist) < 21:
        return False

    vol20 = hist["Volume"].iloc[-21:-1].mean()
    return hist["Volume"].iloc[-1] > vol20


# ---------------------------------------------------------
# Filter 2 — Big Breakdown Candle (Strong body)
# ---------------------------------------------------------
def big_breakdown_candle(hist):
    c = hist.iloc[-1]
    body = abs(c["Close"] - c["Open"])
    full = c["High"] - c["Low"]

    if full == 0:
        return False

    body_ratio = body / full

    # Strong body candle: body must be >60% of total candle range
    return body_ratio >= 0.60 and c["Close"] < c["Open"]


# ---------------------------------------------------------
# Filter 3 — Grinding Trend Filter (Avoid small candles downtrend)
# ---------------------------------------------------------
def avoid_grinding(hist):
    last10 = hist.tail(10)
    avg_body = (last10["Close"] - last10["Open"]).abs().mean()
    last_body = abs(hist["Close"].iloc[-1] - hist["Open"].iloc[-1])

    return last_body > avg_body * 1.2


# ---------------------------------------------------------
# Filter 4 — Retest Failure Filter
# Price must bounce toward EMA and fail
# ---------------------------------------------------------
def retest_failure_filter(hist):
    if len(hist) < 25:
        return False

    hist["EMA9"] = hist["Close"].ewm(span=9).mean()
    hist["EMA20"] = hist["Close"].ewm(span=20).mean()

    last10 = hist.iloc[-11:-1]
    today = hist.iloc[-1]

    bounced = (last10["High"] > last10["EMA9"]) | (last10["High"] > last10["EMA20"])

    rejection_today = today["Close"] < today["Open"]

    return bounced.any() and rejection_today


# ---------------------------------------------------------
# OLD FILTER — Last 3 Candle Lower Lows
# ---------------------------------------------------------
def last_three_candle_lower_lows(hist):
    if len(hist) < 4:
        return False

    c0 = hist["Close"].iloc[-1]
    c1 = hist["Close"].iloc[-2]
    c2 = hist["Close"].iloc[-3]
    c3 = hist["Close"].iloc[-4]

    return c0 < c1 and c1 < c2 and c2 < c3


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Fetching IPOs from NSE...")

    ipo_symbols, equity_df = get_recent_ipos(MIN_LISTING_DAYS)
    print(f"Found {len(ipo_symbols)} eligible IPOs:", ipo_symbols)

    for sym in ipo_symbols:
        print(f"\nChecking: {sym}")

        try:
            series = equity_df[equity_df["SYMBOL"] == sym]["SERIES"].iloc[0]
            if series != "EQ":
                print(f"Skipping (Not shortable): {sym}")
                continue
        except:
            pass

        hist = fetch_history(sym)
        if hist is None:
            print("No data:", sym)
            continue

        atl_info = compute_atl_excluding_today(hist)
        if not atl_info:
            print("ATL error:", sym)
            continue

        atl, atl_idx, atl_pos, total = atl_info
        current = hist["Close"].iloc[-1]

        threshold_ok = current <= atl * (1 + THRESHOLD)
        ll_ok = last_three_candle_lower_lows(hist)
        vol_ok = volume_spike_filter(hist)
        big_candle_ok = big_breakdown_candle(hist)
        grind_ok = avoid_grinding(hist)
        retest_ok = retest_failure_filter(hist)

        # MASTER CONDITION — All strong filters together
        if (
            threshold_ok
            and ll_ok
            and vol_ok
            and big_candle_ok
            and grind_ok
            and retest_ok
        ):
            msg = (
                f"🚨 *High-Quality Breakdown Detected*\n"
                f"*Symbol:* {sym}\n"
                f"*CMP:* {current:.2f}\n"
                f"*ATL:* {atl:.2f}\n"
                f"*ATL Date:* {atl_idx.date()}\n"
                f"*ATL Age:* {total - atl_pos} candles ago\n"
                f"📌 Volume Spike ✓\n"
                f"📌 Big Breakdown Candle ✓\n"
                f"📌 Retest Failure ✓\n"
                f"📌 No Grinding Trend ✓"
            )

            print("ALERT:", sym)
            send_telegram(msg)

    print("\n✔ Scan Complete")
