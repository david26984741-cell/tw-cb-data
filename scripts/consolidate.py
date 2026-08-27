#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把逐日 CSV 合併成回測用的單一檔案。

為什麼需要這一步：
    回測在雲端跑、直接讀 GitHub raw。若逐日去抓，一次回測要發近五千次
    HTTP 請求，慢且會撞到 raw 的流量限制。合併成一個檔，回測只需下載一次。

產出（data/combined/）：
    cb_quotes_all.parquet     全期，回測主要讀這個
    cb_quotes_{年}.parquet    分年，只跑某段期間時用
    manifest.json             筆數、日期範圍、檔案大小、產生時間

回測端用法：
    import pandas as pd
    url = ("https://raw.githubusercontent.com/david26984741-cell/tw-cb-data/"
           "main/data/combined/cb_quotes_all.parquet")
    df = pd.read_parquet(url)
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTES = os.path.join(ROOT, "data", "quotes")
OUT = os.path.join(ROOT, "data", "combined")

# GitHub 單檔硬上限 100MB、50MB 起會警告
WARN_MB = 50
HARD_MB = 100

# 數值欄位：來源是帶尾隨空白與千分位的字串
NUM_COLS = ["收市", "漲跌", "開市", "最高", "最低", "筆數", "單位", "金額",
            "均價", "明日參價", "明日漲停", "明日跌停"]


def read_year(year):
    files = sorted(glob.glob(os.path.join(QUOTES, str(year), "*.csv")))
    if not files:
        return None
    frames = []
    for f in files:
        try:
            frames.append(pd.read_csv(f, dtype=str))
        except Exception as e:
            print(f"  [讀取失敗] {os.path.basename(f)}: {e}", flush=True)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def clean(df):
    df = df.copy()

    df["資料日期"] = pd.to_datetime(df["資料日期"], errors="coerce")
    df["代號"] = df["代號"].astype(str).str.strip()
    df["名稱"] = df["名稱"].astype(str).str.strip()
    df["交易"] = df["交易"].astype(str).str.strip()

    for c in NUM_COLS:
        if c in df.columns:
            s = (df[c].astype(str)
                 .str.replace(",", "", regex=False)
                 .str.strip()
                 .replace({"": None, "--": None, "-": None, "nan": None}))
            df[c] = pd.to_numeric(s, errors="coerce")

    # 來源檔最後有一列「合計」，是當日全市場總量，不是債券。
    # 留在原始 CSV 裡當檢核碼（validate.py 會用），但不能進回測資料。
    df = df[df["代號"].str.fullmatch(r"[0-9A-Za-z]+", na=False)]

    # 代號 + 日期 + 交易別 唯一
    df = df.drop_duplicates(subset=["代號", "資料日期", "交易"], keep="last")
    df = df.sort_values(["資料日期", "代號", "交易"]).reset_index(drop=True)

    # 低基數字串轉 category：parquet 會保留，讀進來記憶體少一大半。
    # Colab 免費版只有 12GB，這一步讓載入從約 640MB 降到約 150MB。
    for c in ("代號", "名稱", "交易"):
        df[c] = df[c].astype("category")
    return df


def write(df, path):
    df.to_parquet(path, index=False, compression="zstd")
    return os.path.getsize(path) / 1024 / 1024


def main():
    os.makedirs(OUT, exist_ok=True)
    years = sorted(int(os.path.basename(d)) for d in glob.glob(os.path.join(QUOTES, "*"))
                   if os.path.basename(d).isdigit())
    if not years:
        print("找不到任何日行情，先跑回補", file=sys.stderr)
        return 1

    all_frames, per_year = [], {}
    for y in years:
        raw = read_year(y)
        if raw is None:
            print(f"{y}  (無檔案，略過)", flush=True)
            continue
        df = clean(raw)
        mb = write(df, os.path.join(OUT, f"cb_quotes_{y}.parquet"))
        per_year[y] = {"列數": len(df), "檔數": len(set(df["資料日期"])),
                       "個券數": df["代號"].nunique(), "MB": round(mb, 2)}
        print(f"{y}  {len(df):>7,} 列  {df['代號'].nunique():>4} 檔  {mb:6.2f} MB", flush=True)
        all_frames.append(df)

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.sort_values(["資料日期", "代號", "交易"]).reset_index(drop=True)
    all_path = os.path.join(OUT, "cb_quotes_all.parquet")
    mb = write(combined, all_path)

    d0, d1 = combined["資料日期"].min(), combined["資料日期"].max()
    tpe = timezone(timedelta(hours=8))

    manifest = {
        "產生時間": datetime.now(tpe).strftime("%Y-%m-%d %H:%M:%S%z"),
        "總列數": int(len(combined)),
        "交易日數": int(combined["資料日期"].nunique()),
        "個券數": int(combined["代號"].nunique()),
        "日期範圍": [d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")],
        "全期檔MB": round(mb, 2),
        "分年": per_year,
    }
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 56)
    print(f"全期  {len(combined):,} 列 / {combined['資料日期'].nunique():,} 交易日 / "
          f"{combined['代號'].nunique():,} 檔")
    print(f"      {d0:%Y-%m-%d} ~ {d1:%Y-%m-%d}")
    print(f"      {mb:.2f} MB -> {os.path.relpath(all_path, ROOT)}")

    if mb >= HARD_MB:
        print(f"\n[錯誤] 超過 GitHub 單檔 {HARD_MB}MB 上限，回測請改讀分年檔",
              file=sys.stderr)
        return 1
    if mb >= WARN_MB:
        print(f"\n[注意] 已超過 {WARN_MB}MB，GitHub 會警告。接近 {HARD_MB}MB 時要改分年或改用 Release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
