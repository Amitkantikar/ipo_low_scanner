import os
import requests
import pandas as pd
import yfinance as yf
from io import BytesIO
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

MIN_LISTING_DAYS = 150
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


# ---------------------------------
# NEW: Exclude today's candle
# ---------------------------------
def compute_atl_excluding_today(hist):
    try:
        hist_no_today = hist.iloc[:-1]

        atl = hist_no_today["Low"].min()
        idx = hist_no_today["Low"].idxmin()
        pos = hist_no_today.index.get_loc(idx)

        return atl, idx, pos, len(hist_no_today)
    except:
        return None


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

        # -----------------------------------------
        # NEW FILTER: ATL must be older than 3 candles
        # -----------------------------------------
        if atl_pos > total - 4:
            print(f"Skipping {sym} (ATL too recent: within last 3 candles)")
            continue

        # Breakdown condition
        if current <= atl * (1 + THRESHOLD):
            msg = (
                f"🚨 *Breakdown Detected*\n"
                f"*Symbol:* {sym}\n"
                f"*CMP:* {current:.2f}\n"
                f"*ATL:* {atl:.2f}\n"
                f"*ATL Date:* {atl_idx.date()}\n"
                f"*ATL Age:* {total - atl_pos} candles ago\n"
            )

            print("ALERT:", sym)
            send_telegram(msg)

    print("\n✔ Scan Complete")
