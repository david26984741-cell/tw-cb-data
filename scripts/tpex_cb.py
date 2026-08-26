#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
櫃買中心 轉(交)換公司債 日行情 抓取與解析。

端點與格式細節見 README.md 第 1 節（2026-08-26 以本機瀏覽器實地驗證）。

用法:
    python tpex_cb.py index 2026-08          列出該月有哪些檔
    python tpex_cb.py fetch 2026-08-25       抓單日並解析成 UTF-8 CSV
    python tpex_cb.py backfill               回補 2007-01 至今（可中斷續跑）
    python tpex_cb.py backfill --start 2015-01 --end 2016-12
"""

import argparse
import csv
import io
import os
import sys
import time
from datetime import date, datetime

import requests

BASE = "https://www.tpex.org.tw"
INDEX_URL = f"{BASE}/www/zh-tw/bond/cbDaily"
FILE_CODE = "rsta0113"           # 每日轉(交)換公司債買賣斷交易行情表
HISTORY_START = (2007, 1)        # 已驗證最早為 2007-01-02

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "quotes")

SLEEP = float(os.environ.get("SLEEP_TPEX", "1.5"))
TIMEOUT = 30

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36"),
    "Referer": f"{BASE}/zh-tw/bond/info/statistics-cb/day.html",
    "X-Requested-With": "XMLHttpRequest",
}

_session = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        # 先取一次頁面，讓伺服器發 cookie（若它有這個要求）
        try:
            _session.get(HEADERS["Referer"], timeout=TIMEOUT)
        except requests.RequestException:
            pass
    return _session


# ---------------------------------------------------------------- 索引

def month_index(year, month, file_code=FILE_CODE):
    """回傳該月的 [(西元日期字串 YYYYMMDD, 檔案路徑), ...]，新到舊。

    date 參數送該月任一天即可，伺服器一律回整月。
    """
    payload = {
        "date": f"{year:04d}/{month:02d}/01",
        "fileCode": file_code,
        "id": "",
        "response": "json",
    }
    r = session().post(INDEX_URL, data=payload, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if j.get("stat") != "ok":
        raise RuntimeError(f"{year}-{month:02d} 索引回應非 ok: {j.get('stat')!r}")

    out = []
    for table in j.get("tables") or []:
        for row in table.get("data") or []:
            if len(row) < 2:
                continue
            path = row[1]
            ymd = _ymd_from_path(path)
            if ymd:
                out.append((ymd, path))
    return out


def _ymd_from_path(path):
    """從 .../RSta0113.20260825-C.csv 取出 20260825。"""
    base = os.path.basename(path)
    for part in base.replace("-", ".").split("."):
        if len(part) == 8 and part.isdigit():
            return part
    return None


def static_url(ymd, file_code=FILE_CODE):
    """由日期直接組出靜態檔網址，不需先打索引 API。

    注意檔名大小寫與參數不同：參數全小寫 rsta0113，檔名為 RSta0113。
    """
    name = file_code[0].upper() + file_code[1].upper() + file_code[2:]
    return (f"{BASE}/storage/bond_zone/tradeinfo/cb/"
            f"{ymd[:4]}/{ymd[:6]}/{name}.{ymd}-C.csv")


# ---------------------------------------------------------------- 下載與解析

def download(path_or_url):
    url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
    r = session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def parse(raw, ymd=None):
    """把櫃買的標籤式 Big5 CSV 解析成 list[dict]。

    格式（README 1.4）：每行第一欄是列型標籤。
        HEADER,代號,名稱,交易,收市,...
        BODY,"11011","台泥一永  ","等價",...
        BODY,"","","議價",...

    一檔 CB 佔兩列（等價／議價），代號與名稱只出現在第一列，需前向填補。
    """
    text = raw.decode("big5", errors="replace")
    header = None
    rows = []
    last_code = last_name = ""

    for line in csv.reader(io.StringIO(text)):
        if not line:
            continue
        tag, rest = line[0].strip(), [c.strip() for c in line[1:]]

        if tag == "HEADER":
            header = rest
            continue
        if tag != "BODY" or header is None:
            continue

        # 補齊長度差異，避免尾欄缺漏時錯位
        if len(rest) < len(header):
            rest += [""] * (len(header) - len(rest))

        rec = dict(zip(header, rest[:len(header)]))

        # 前向填補：第二列（議價）的代號與名稱為空
        code = rec.get("代號", "")
        name = rec.get("名稱", "")
        if code:
            last_code, last_name = code, name
        else:
            rec["代號"], rec["名稱"] = last_code, last_name

        if not rec.get("代號"):
            continue  # 尚未遇到任何有代號的列，跳過

        if ymd:
            rec = {"資料日期": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", **rec}
        rows.append(rec)

    if header is None:
        raise ValueError("找不到 HEADER 列，檔案格式可能已變更")
    return rows


def out_path(ymd):
    return os.path.join(OUT_DIR, ymd[:4], f"RSta0113.{ymd}.csv")


def save(rows, ymd):
    p = out_path(ymd)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not rows:
        return p
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


def fetch_day(ymd, force=False):
    p = out_path(ymd)
    if os.path.exists(p) and not force:
        return p, 0, True          # 已存在，略過
    rows = parse(download(static_url(ymd)), ymd)
    save(rows, ymd)
    return p, len(rows), False


# ---------------------------------------------------------------- CLI

def _months(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _parse_ym(s):
    d = datetime.strptime(s, "%Y-%m")
    return d.year, d.month


def cmd_index(args):
    y, m = _parse_ym(args.month)
    items = month_index(y, m)
    print(f"{y}-{m:02d}  共 {len(items)} 個檔")
    for ymd, path in items:
        print(f"  {ymd}  {path}")


def cmd_fetch(args):
    ymd = datetime.strptime(args.day, "%Y-%m-%d").strftime("%Y%m%d")
    p, n, skipped = fetch_day(ymd, force=args.force)
    print("已存在，略過：" + p if skipped else f"{n} 列 -> {p}")


def cmd_backfill(args):
    start = _parse_ym(args.start) if args.start else HISTORY_START
    today = date.today()
    end = _parse_ym(args.end) if args.end else (today.year, today.month)

    total_files = total_rows = total_skip = 0
    failures = []

    for y, m in _months(start, end):
        try:
            items = month_index(y, m)
        except Exception as e:
            print(f"[索引失敗] {y}-{m:02d}: {e}", file=sys.stderr)
            failures.append((f"{y}-{m:02d}", "index", str(e)))
            time.sleep(SLEEP)
            continue

        got = skipped = 0
        for ymd, path in items:
            try:
                p = out_path(ymd)
                if os.path.exists(p) and not args.force:
                    skipped += 1
                    continue
                rows = parse(download(path), ymd)
                save(rows, ymd)
                got += 1
                total_rows += len(rows)
                time.sleep(SLEEP)
            except Exception as e:
                print(f"[下載失敗] {ymd}: {e}", file=sys.stderr)
                failures.append((ymd, "download", str(e)))
                time.sleep(SLEEP)

        total_files += got
        total_skip += skipped
        print(f"{y}-{m:02d}  索引 {len(items):>2}  新抓 {got:>2}  略過 {skipped:>2}")
        time.sleep(SLEEP)

    print(f"\n完成：新抓 {total_files} 檔 / {total_rows} 列，略過 {total_skip} 檔，失敗 {len(failures)} 次")
    if failures:
        fp = os.path.join(ROOT, "data", "backfill_failures.csv")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows([("target", "stage", "error")] + failures)
        print(f"失敗清單：{fp}（重跑本指令即可續補，已存在的檔會自動略過）")


def main():
    ap = argparse.ArgumentParser(description="櫃買 CB 日行情抓取")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("index", help="列出某月有哪些檔")
    p1.add_argument("month", help="YYYY-MM")
    p1.set_defaults(func=cmd_index)

    p2 = sub.add_parser("fetch", help="抓單日")
    p2.add_argument("day", help="YYYY-MM-DD")
    p2.add_argument("--force", action="store_true")
    p2.set_defaults(func=cmd_fetch)

    p3 = sub.add_parser("backfill", help="回補區間")
    p3.add_argument("--start", help="YYYY-MM，預設 2007-01")
    p3.add_argument("--end", help="YYYY-MM，預設本月")
    p3.add_argument("--force", action="store_true")
    p3.set_defaults(func=cmd_backfill)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
