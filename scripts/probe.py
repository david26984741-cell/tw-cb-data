#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
連線與端點體檢。動任何回補之前先跑這支。

    python scripts/probe.py

會印出對外 IP、各端點 PASS/FAIL，並把原始回應片段寫進 data/probe_report.txt。
櫃買部分已在瀏覽器驗證過，這裡是確認「純 Python、無瀏覽器 session」是否同樣可行。
MOPS 部分完全未驗證，輸出的片段是用來決定接下來怎麼寫。
"""

import io
import os
import sys
import csv
import json
import traceback

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "data", "probe_report.txt")

TPEX = "https://www.tpex.org.tw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0 Safari/537.36")

log_lines = []


def log(s=""):
    print(s)
    log_lines.append(str(s))


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
        log_lines.append(traceback.format_exc())
    log(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        for line in str(detail).splitlines():
            log(f"        {line}")
    return ok


# ---------------------------------------------------------------- 對外 IP

def egress_ip():
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip",
              "https://checkip.amazonaws.com"):
        try:
            r = requests.get(u, timeout=10)
            if r.ok:
                return True, r.text.strip()
        except requests.RequestException:
            continue
    return False, "三個查 IP 的服務都不通（不影響後續測試）"


# ---------------------------------------------------------------- 櫃買

def tpex_robots():
    r = requests.get(f"{TPEX}/robots.txt", headers={"User-Agent": UA}, timeout=20)
    return r.status_code in (200, 404), f"HTTP {r.status_code}（404 代表沒有 robots.txt，符合先前紀錄）"


def tpex_static_bare():
    """關鍵測試：完全不帶 cookie / referer，直接抓靜態 CSV。"""
    u = f"{TPEX}/storage/bond_zone/tradeinfo/cb/2026/202608/RSta0113.20260825-C.csv"
    r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
    head = r.content[:80].decode("big5", errors="replace")
    ok = r.ok and r.content[:5] == b"TITLE"
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes\n開頭: {head}"


def tpex_static_earliest():
    u = f"{TPEX}/storage/bond_zone/tradeinfo/cb/2007/200701/RSta0113.20070102-C.csv"
    r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
    ok = r.ok and r.content[:5] == b"TITLE"
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes（驗證 2007-01-02 起點）"


def tpex_index_post():
    """索引 API：POST + 表單參數。用 GET 會回『參數輸入錯誤』。"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": f"{TPEX}/zh-tw/bond/info/statistics-cb/day.html",
        "X-Requested-With": "XMLHttpRequest",
    })
    r = s.post(f"{TPEX}/www/zh-tw/bond/cbDaily",
               data={"date": "2026/08/01", "fileCode": "rsta0113",
                     "id": "", "response": "json"}, timeout=30)
    j = r.json()
    n = sum(len(t.get("data") or []) for t in (j.get("tables") or []))
    ok = j.get("stat") == "ok" and n > 0
    return ok, f"HTTP {r.status_code}  stat={j.get('stat')!r}  date={j.get('date')!r}  檔數={n}"


def tpex_index_no_referer():
    """索引 API 是否依賴 Referer / session。"""
    r = requests.post(f"{TPEX}/www/zh-tw/bond/cbDaily",
                      headers={"User-Agent": UA},
                      data={"date": "2026/08/01", "fileCode": "rsta0113",
                            "id": "", "response": "json"}, timeout=30)
    try:
        j = r.json()
        ok = j.get("stat") == "ok"
        return ok, f"HTTP {r.status_code}  stat={j.get('stat')!r}（若 PASS，抓取器可省掉暖身請求）"
    except ValueError:
        return False, f"HTTP {r.status_code}  非 JSON: {r.text[:120]}"


def tpex_parse():
    """實際解析一天，確認 BODY 兩列結構與前向填補。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import tpex_cb
    rows = tpex_cb.parse(tpex_cb.download(tpex_cb.static_url("20260825")), "20260825")
    codes = {r["代號"] for r in rows}
    blanks = sum(1 for r in rows if not r["代號"])
    types = {}
    for r in rows:
        types[r.get("交易", "")] = types.get(r.get("交易", ""), 0) + 1
    ok = len(rows) > 100 and blanks == 0 and len(codes) > 50
    return ok, (f"{len(rows)} 列 / {len(codes)} 檔 / 空代號 {blanks} 列\n"
                f"交易別分布: {types}\n首列: {list(rows[0].items())[:4]}")


# ---------------------------------------------------------------- MOPS（未驗證）

MOPS_HOSTS = ["https://mopsov.twse.com.tw", "https://mops.twse.com.tw"]


def mops_reachable():
    results = []
    any_ok = False
    for h in MOPS_HOSTS:
        try:
            r = requests.get(h + "/mops/web/index", headers={"User-Agent": UA},
                             timeout=25, allow_redirects=True)
            results.append(f"{h} -> HTTP {r.status_code}  final={r.url[:70]}")
            any_ok = any_ok or r.ok
        except Exception as e:
            results.append(f"{h} -> {type(e).__name__}")
    return any_ok, "\n".join(results)


def mops_t120sg01():
    """驗證交接文件的宣稱：缺 bond_yrn / bond_subn 會回「債券期參數錯誤」。

    以 12101（大成長城一）為例：前 4 碼 1210 是發行人股票代號，第 5 碼起 1 是期別。
    """
    out = []
    ok_any = False
    for h in MOPS_HOSTS:
        url = h + "/mops/web/ajax_t120sg01"
        base = {"encodeURIComponent": "1", "step": "1", "firstin": "1",
                "off": "1", "TYPEK": "all", "issuer_stock_code": "1210"}
        for label, extra in (("缺 bond_yrn/bond_subn", {}),
                             ("完整參數", {"bond_yrn": "1", "bond_subn": "$M00000001"})):
            try:
                r = requests.post(url, data={**base, **extra},
                                  headers={"User-Agent": UA}, timeout=25)
                txt = r.content.decode("big5", errors="replace")
                mark = "債券期參數錯誤" if "債券期參數錯誤" in txt else ""
                out.append(f"{h} [{label}] HTTP {r.status_code} {len(r.content)}B {mark}")
                out.append("    " + " ".join(txt.split())[:160])
                ok_any = ok_any or r.ok
            except Exception as e:
                out.append(f"{h} [{label}] {type(e).__name__}")
    return ok_any, "\n".join(out)


# ---------------------------------------------------------------- 其他來源

def tdcc():
    r = requests.get("https://opendata.tdcc.com.tw/getOD.ashx",
                     params={"id": "2-8"}, headers={"User-Agent": UA}, timeout=40)
    n = r.text.count("\n")
    return r.ok, f"HTTP {r.status_code}  {len(r.content)} bytes  約 {n} 列（實測只回當月，不存就沒了）"


def cb168():
    r = requests.get("https://cb168.netlify.app/output.xlsx",
                     headers={"User-Agent": UA}, timeout=30)
    ok = r.ok and r.content[:2] == b"PK"
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes（可與官方母體 2,412 檔互相校驗）"


# ---------------------------------------------------------------- main

def main():
    log("=" * 62)
    log(" tw-cb-data 連線體檢")
    log("=" * 62)

    log("\n-- 環境 --")
    check("對外 IP", egress_ip)

    log("\n-- 櫃買 TPEx（瀏覽器已驗證，此處測純 Python）--")
    r1 = check("robots.txt", tpex_robots)
    r2 = check("靜態 CSV（不帶 cookie/referer）", tpex_static_bare)
    r3 = check("靜態 CSV 2007-01-02（歷史起點）", tpex_static_earliest)
    r4 = check("索引 API POST（帶 referer）", tpex_index_post)
    check("索引 API POST（不帶 referer）", tpex_index_no_referer)
    r6 = check("解析器對真實檔案", tpex_parse)

    log("\n-- MOPS（完全未驗證，以下是探路）--")
    check("MOPS 可達性", mops_reachable)
    check("t120sg01 參數規則", mops_t120sg01)

    log("\n-- 其他來源 --")
    check("集保 opendata 2-8", tdcc)
    check("cb168 output.xlsx", cb168)

    log("\n" + "=" * 62)
    log(" 判讀")
    log("=" * 62)
    if r2 and r3 and r6:
        log(" 櫃買回補可以直接開跑：")
        log("     python scripts/tpex_cb.py backfill")
        if not r4:
            log(" 索引 API 不通但靜態檔可抓 —— 改用日期推導網址，")
            log("     代價是要自行處理休市日（抓到 404 就跳過即可）。")
    elif r1 and not r2:
        log(" 能連上櫃買但抓不到靜態檔 —— 多半是限速或 UA 檢查。")
        log("     把環境變數 SLEEP_TPEX 調到 8~10 再試。")
    else:
        log(" 櫃買整體不通。先確認這台機器能否用瀏覽器開 tpex.org.tw，")
        log("     若瀏覽器可以而 Python 不行，通常是公司網路的 TLS 攔截或 Proxy。")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"\n完整報告：{REPORT}")


if __name__ == "__main__":
    main()
