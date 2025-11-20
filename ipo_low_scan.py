import os
import requests
import pandas as pd
import yfinance as yf
from io import BytesIO
from datetime import datetime, timedelta
import warnings


warnings.filterwarnings("ignore", category=FutureWarning)

# --------------------------
# CONFIG
# --------------------------
MIN_LISTING_DAYS = 150
THRESHOLD = 0.01
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")


# --------------------------
# Telegram
# --------------------------
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass


# -----------------------------------------------------------
# Load NSE equity list
# -----------------------------------------------------------
def fetch_equity_list():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    r = requests.get(url, timeout=40)
    df = pd.read_csv(BytesIO(r.content))
    df.columns = df.columns.str.strip()
    return df


# -----------------------------------------------------------
# Filter IPOs and keep EQ (shortable)
# -----------------------------------------------------------
def get_recent_ipos(days: int):
    df = fetch_equity_list()

    df["DATE OF LISTING"] = pd.to_datetime(df["DATE OF LISTING"], errors="coerce")
    df = df.dropna(subset=["DATE OF LISTING"])

    cutoff = datetime.now() - timedelta(days=days)
    recent = df[df["DATE OF LISTING"] >= cutoff]

    recent = recent[recent["SERIES"] == "EQ"]  # only shortable stocks

    return recent["SYMBOL"].tolist(), recent


# -----------------------------------------------------------
# Fetch price history
# -----------------------------------------------------------
def fetch_history(symbol):
    try:
        hist = yf.Ticker(symbol + ".NS").history(period="max")
        return hist if not hist.empty else None
    except:
        return None


# -----------------------------------------------------------
# Compute ATL
# -----------------------------------------------------------
def compute_atl(hist):
    try:
        atl = hist["Low"].min()
        idx = hist["Low"].idxmin()
        pos = hist.index.get_loc(idx)
        return atl, idx, pos, len(hist)
    except:
        return None


# -----------------------------------------------------------
# TOP 2 ADVANCED BREAKDOWN FILTERS
# -----------------------------------------------------------

# 1️⃣ Volume Dry-Up Breakdown (VDB)
def volume_dry_up(hist):
    if len(hist) < 20:
        return False

    vol20 = hist["Volume"].tail(20).mean()
    vol5 = hist["Volume"].tail(5).mean()

    return vol5 < vol20 * 0.60


# 2️⃣ Range Expansion Breakdown (REB)
def range_expansion_breakdown(hist):
    if len(hist) < 12:
        return False

    today = hist.iloc[-1]
    prev = hist.iloc[-2]

    body_today = abs(today["Close"] - today["Open"])
    avg_body_10 = (hist["Close"] - hist["Open"]).abs().tail(11).head(10).mean()

    return (body_today > avg_body_10 * 1.3) and (today["Close"] < prev["Low"])


# ======================================================================
# MAIN WORKFLOW
# ======================================================================
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

        atl_info = compute_atl(hist)
        if not atl_info:
            print("ATL error:", sym)
            continue

        atl, atl_idx, atl_pos, total = atl_info
        current = hist["Close"].iloc[-1]

        # Apply top 2 advanced breakdown filters
        vdb = volume_dry_up(hist)
        reb = range_expansion_breakdown(hist)

        # Main breakdown condition + advanced filters
        if current <= atl * (1 + THRESHOLD) and (vdb or reb):
            msg = (
                f"🚨 *POWERFUL Breakdown Detected!*\n"
                f"*Symbol:* {sym}\n"
                f"*CMP:* {current:.2f}\n"
                f"*ATL:* {atl:.2f}\n\n"
                f"*Volume Dry-Up:* {'Yes' if vdb else 'No'}\n"
                f"*Range Expansion Breakdown:* {'Yes' if reb else 'No'}\n"
            )

            print("ALERT:", sym)
            send_telegram(msg)

    print("\n✔ Scan Complete")
