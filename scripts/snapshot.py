#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
櫃買 CB 狀態快照與公告回補。

為什麼要每天跑：
    下櫃、上櫃、變更/停止交易、賣回權這四個端點都是「滾動視窗」——
    只保留最近一段時間，舊的會被擠掉且無法回溯。實測下櫃只留 47 筆
    （約三個月）。不每天存，事件就永久消失。

    停止轉換與債息則是檔案索引，跟日行情一樣可以回補歷史。

端點（皆為 POST /www/zh-tw/bond/{code}，body 只需 response=json）：
    convDelist    最近下櫃          滾動快照
    convSearch    最近上櫃          滾動快照（含 MOPS 條款連結）
    cbMode        變更/分盤/停止交易 滾動快照
    putProvision  賣回權            滾動快照
    cbSuspend     停止轉換          檔案索引（逐日）
    cbCoupon      債息              檔案索引（逐年）

產出：
    data/snapshots/{名稱}/{YYYY-MM-DD}.csv   當日原樣快照
    data/snapshots/{名稱}_history.csv        累積表，含首見／末見日
    data/suspend/{YYYY}/RSdrs002.{YYYYMMDD}.csv
    data/coupon/rsta0236.{年}.csv

用法：
    python scripts/snapshot.py            四個快照 + 兩個索引的增量
    python scripts/snapshot.py --backfill 連同停止轉換／債息的歷史一起補
"""

import argparse
import csv
import io
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tpex_cb  # 共用 SSL 設定、重試、Big5 解析

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = tpex_cb.ROOT
BASE = tpex_cb.BASE
SNAP = os.path.join(ROOT, "data", "snapshots")

SLEEP = float(os.environ.get("SLEEP_TPEX", "1.5"))

# 滾動快照類：不每天存就永久消失
TABLES = {
    "delist": ("convDelist", "最近下櫃"),
    "listed": ("convSearch", "最近上櫃"),
    "mode": ("cbMode", "變更分盤停止交易"),
    "put": ("putProvision", "賣回權"),
}

# 檔案索引類：可回補歷史
INDEXES = {
    "suspend": ("cbSuspend", "停止轉換", os.path.join(ROOT, "data", "suspend")),
    "coupon": ("cbCoupon", "債息", os.path.join(ROOT, "data", "coupon")),
}


def call(code):
    def _c():
        r = tpex_cb.session().post(
            f"{BASE}/www/zh-tw/bond/{code}",
            data={"response": "json"}, timeout=tpex_cb.TIMEOUT)
        r.raise_for_status()
        j = r.json()
        if j.get("stat") != "ok":
            raise RuntimeError(f"回應非 ok: {j.get('stat')!r}")
        return j

    j = tpex_cb._retry(_c, code)
    tb = (j.get("tables") or [{}])[0]
    return tb.get("fields") or [], tb.get("data") or []


# ------------------------------------------------------------ 滾動快照

def snap_table(key, today):
    code, label = TABLES[key]
    fields, rows = call(code)

    d = os.path.join(SNAP, key)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{today}.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["抓取日期"] + fields)
        for r in rows:
            w.writerow([today] + list(r))

    added = merge_history(key, fields, rows, today)
    print(f"  {label:<12} 當日 {len(rows):>4} 筆    累積新增 {added:>3} 筆", flush=True)
    return len(rows), added


def merge_history(key, fields, rows, today):
    """累積表：同一筆只記一次，但保留首見與末見日期。

    滾動視窗會把舊資料擠掉，所以累積表才是真正的歷史，
    而首見／末見日期讓你知道這筆是什麼時候被觀察到的。
    """
    path = os.path.join(SNAP, f"{key}_history.csv")
    header = ["首見日期", "末見日期"] + fields
    existing = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for rec in csv.DictReader(f):
                k = tuple(rec.get(c, "") for c in fields)
                existing[k] = rec

    added = 0
    for r in rows:
        k = tuple(str(x) for x in r)
        if k in existing:
            existing[k]["末見日期"] = today
        else:
            rec = {"首見日期": today, "末見日期": today}
            rec.update(dict(zip(fields, [str(x) for x in r])))
            existing[k] = rec
            added += 1

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for rec in sorted(existing.values(), key=lambda x: x["首見日期"]):
            w.writerow(rec)
    return added


# ------------------------------------------------------------ 檔案索引

def snap_index(key, backfill):
    code, label, outdir = INDEXES[key]
    fields, rows = call(code)

    # 每列可能有多個檔案欄位（xls 與 csv），只取 .csv
    paths = []
    for r in rows:
        for cell in r:
            s = str(cell)
            if s.startswith("/storage") and s.lower().endswith(".csv"):
                paths.append(s)

    got = skipped = failed = 0
    for path in paths:
        stem = os.path.basename(path).replace("-C.csv", "")
        tail = stem.split(".")[-1]                   # 20260827 或 2010
        sub = tail[:4] if len(tail) == 8 and tail.isdigit() else ""
        dest = os.path.join(outdir, sub, stem.replace(".", "_") + ".csv")

        if os.path.exists(dest):
            skipped += 1
            continue
        try:
            raw = tpex_cb.session().get(BASE + path, timeout=tpex_cb.TIMEOUT).content
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8", newline="") as f:
                f.write(raw.decode("big5", errors="replace"))
            got += 1
            time.sleep(SLEEP)
        except Exception as e:
            print(f"    [下載失敗] {name}: {e}", file=sys.stderr, flush=True)
            failed += 1

    print(f"  {label:<12} 索引 {len(paths):>4} 檔    新抓 {got:>3}  略過 {skipped:>3}"
          + (f"  失敗 {failed}" if failed else ""), flush=True)
    return got


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="櫃買 CB 狀態快照")
    ap.add_argument("--backfill", action="store_true",
                    help="連同停止轉換／債息的歷史檔一起補")
    args = ap.parse_args()

    today = date.today().isoformat()
    print(f"抓取日期 {today}\n")

    print("滾動快照（不存就消失）：")
    for key in TABLES:
        try:
            snap_table(key, today)
        except Exception as e:
            print(f"  [失敗] {TABLES[key][1]}: {e}", file=sys.stderr, flush=True)
        time.sleep(SLEEP)

    print("\n檔案索引（可回補）：")
    for key in INDEXES:
        try:
            snap_index(key, args.backfill)
        except Exception as e:
            print(f"  [失敗] {INDEXES[key][1]}: {e}", file=sys.stderr, flush=True)
        time.sleep(SLEEP)

    print("\n累積表：")
    for key, (_, label) in TABLES.items():
        p = os.path.join(SNAP, f"{key}_history.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                n = sum(1 for _ in f) - 1
            print(f"  {label:<12} 累積 {n:>5} 筆  ({os.path.relpath(p, ROOT)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
