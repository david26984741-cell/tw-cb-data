#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
連線與端點體檢。動任何回補之前先跑這支。

    python scripts/probe.py

輸出：
    data/probe_report.txt   完整報告（UTF-8）
    data/raw/*.html         MOPS 原始回應，供人工判讀用
"""

import os
import sys
import traceback

# Windows 主控台預設 cp950，遇到 U+FFFD 會整支崩掉，強制轉 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tpex_cb  # noqa: E402  取用共用的 SSL 設定與解析器

REPORT = os.path.join(ROOT, "data", "probe_report.txt")
RAW_DIR = os.path.join(ROOT, "data", "raw")

TPEX = tpex_cb.BASE
UA = tpex_cb.HEADERS["User-Agent"]

log_lines = []


def log(s=""):
    s = str(s).replace("�", "?")     # 避免主控台編碼問題
    print(s)
    log_lines.append(s)


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


def save_raw(name, content):
    os.makedirs(RAW_DIR, exist_ok=True)
    p = os.path.join(RAW_DIR, name)
    with open(p, "wb") as f:
        f.write(content)
    return os.path.relpath(p, ROOT)


# ---------------------------------------------------------------- 環境

def egress_ip():
    s = tpex_cb.make_session(with_headers=False)
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip",
              "https://checkip.amazonaws.com"):
        try:
            r = s.get(u, timeout=10)
            if r.ok:
                return True, r.text.strip()
        except requests.RequestException:
            continue
    return False, "三個查 IP 的服務都不通（不影響後續測試）"


def ssl_mode():
    import ssl
    strict = hasattr(ssl, "VERIFY_X509_STRICT")
    return True, (f"Python {sys.version.split()[0]} / {ssl.OPENSSL_VERSION}\n"
                  f"VERIFY_X509_STRICT 存在: {strict}"
                  "（櫃買憑證缺 Subject Key Identifier，本專案已針對此放寬該項檢查）")


# ---------------------------------------------------------------- 櫃買

def tpex_robots():
    r = tpex_cb.make_session(with_headers=False).get(f"{TPEX}/robots.txt", timeout=20)
    return r.status_code in (200, 404), f"HTTP {r.status_code}（404 代表沒有 robots.txt）"


def tpex_static_bare():
    """關鍵測試：不帶 cookie / referer，直接抓靜態 CSV。"""
    u = tpex_cb.static_url("20260825")
    r = tpex_cb.make_session(with_headers=False).get(u, timeout=30)
    ok = r.ok and r.content[:5] == b"TITLE"
    head = r.content[:60].decode("big5", errors="replace")
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes\n開頭: {head}"


def tpex_static_earliest():
    u = tpex_cb.static_url("20070102")
    r = tpex_cb.make_session(with_headers=False).get(u, timeout=30)
    ok = r.ok and r.content[:5] == b"TITLE"
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes（驗證 2007-01-02 起點）"


def tpex_index_post():
    items = tpex_cb.month_index(2026, 8)
    return len(items) > 0, f"2026-08 索引取得 {len(items)} 個檔；最新 {items[0][0] if items else '-'}"


def tpex_index_bare():
    """索引 API 是否依賴 Referer / session。"""
    r = tpex_cb.make_session(with_headers=False).post(
        f"{TPEX}/www/zh-tw/bond/cbDaily",
        data={"date": "2026/08/01", "fileCode": "rsta0113",
              "id": "", "response": "json"}, timeout=30)
    try:
        j = r.json()
        n = sum(len(t.get("data") or []) for t in (j.get("tables") or []))
        return j.get("stat") == "ok", f"HTTP {r.status_code}  stat={j.get('stat')!r}  檔數={n}"
    except ValueError:
        return False, f"HTTP {r.status_code}  非 JSON: {r.text[:120]}"


def tpex_parse():
    rows = tpex_cb.parse(tpex_cb.download(tpex_cb.static_url("20260825")), "20260825")
    codes = {r["代號"] for r in rows}
    blanks = sum(1 for r in rows if not r["代號"])
    kinds = {}
    for r in rows:
        kinds[r.get("交易", "")] = kinds.get(r.get("交易", ""), 0) + 1
    ok = len(rows) > 100 and blanks == 0 and len(codes) > 50
    first = rows[0] if rows else {}
    return ok, (f"{len(rows)} 列 / {len(codes)} 檔 / 空代號 {blanks} 列（須為 0）\n"
                f"交易別分布: {kinds}\n"
                f"首列: 代號={first.get('代號')} 名稱={first.get('名稱')} "
                f"交易={first.get('交易')} 均價={first.get('均價')}")


# ---------------------------------------------------------------- MOPS

MOPS = "https://mopsov.twse.com.tw"


def mops_reachable():
    r = tpex_cb.make_session(with_headers=False).get(
        MOPS + "/mops/web/index", timeout=25, allow_redirects=True)
    return r.ok, f"HTTP {r.status_code}  final={r.url}"


def mops_t120sg01():
    """交接文件宣稱：缺 bond_yrn / bond_subn 會回「債券期參數錯誤」。

    以 12101（大成長城一）為例：前 4 碼 1210 是發行人股票代號、第 5 碼起 1 是期別。
    原始回應會存進 data/raw/，因為要靠它決定正式抓取器怎麼寫。
    """
    s = tpex_cb.make_session(with_headers=False)
    url = MOPS + "/mops/web/ajax_t120sg01"
    base = {"encodeURIComponent": "1", "step": "1", "firstin": "1",
            "off": "1", "TYPEK": "all", "issuer_stock_code": "1210"}
    cases = [("no_bond_yrn", {}),
             ("full", {"bond_yrn": "1", "bond_subn": "$M00000001"})]

    out, ok_any = [], False
    for label, extra in cases:
        r = s.post(url, data={**base, **extra}, timeout=25)
        txt = r.content.decode("big5", errors="replace")
        flat = " ".join(txt.split())
        p = save_raw(f"mops_t120sg01_{label}.html", r.content)
        hit = "有『債券期參數錯誤』" if "債券期參數錯誤" in txt else "無該錯誤字串"
        has_price = "有『轉(交)換價格』" if "轉(交)換價格" in txt else "無轉換價格欄位"
        out.append(f"[{label}] HTTP {r.status_code}  {len(r.content)}B  {hit}  {has_price}")
        out.append(f"    存檔: {p}")
        out.append(f"    內容: {flat[:200]}")
        ok_any = ok_any or r.ok
    return ok_any, "\n".join(out)


def mops_bulk():
    """包裹下載：關鍵是 step=12，且至少要給一個查詢條件。"""
    s = tpex_cb.make_session(with_headers=False)
    url = MOPS + "/mops/web/ajax_t120sb02"
    data = {"encodeURIComponent": "1", "step": "12", "firstin": "1",
            "off": "1", "TYPEK": "all",
            "issuer_stock_code_1": "1000", "issuer_stock_code_2": "9999"}
    r = s.post(url, data=data, timeout=60)
    txt = r.content.decode("big5", errors="replace")
    p = save_raw("mops_bulk_step12.html", r.content)
    need = "『請至少輸入一項查詢項目』" if "至少輸入一項" in txt else ""
    has = "有『認股價格』欄位" if "認股價格" in txt else "無認股價格欄位"
    rows = txt.count("<tr")
    ok = r.ok and len(r.content) > 5000
    return ok, (f"HTTP {r.status_code}  {len(r.content)}B  約 {rows} 個 <tr>  {has} {need}\n"
                f"存檔: {p}\n內容: {' '.join(txt.split())[:200]}")


# ---------------------------------------------------------------- 其他

def tdcc():
    r = tpex_cb.make_session(with_headers=False).get(
        "https://opendata.tdcc.com.tw/getOD.ashx", params={"id": "2-8"}, timeout=60)
    return r.ok, f"HTTP {r.status_code}  {len(r.content)} bytes  約 {r.text.count(chr(10))} 列"


def cb168():
    r = tpex_cb.make_session(with_headers=False).get(
        "https://cb168.netlify.app/output.xlsx", timeout=30)
    ok = r.ok and r.content[:2] == b"PK"
    return ok, f"HTTP {r.status_code}  {len(r.content)} bytes"


# ---------------------------------------------------------------- main

def main():
    # MOPS 的 robots.txt disallow /mops/，在 GitHub Actions 上要跳過。
    skip_mops = "--no-mops" in sys.argv or os.environ.get("SKIP_MOPS") == "1"

    log("=" * 62)
    log(" tw-cb-data 連線體檢")
    log("=" * 62)

    log("\n-- 環境 --")
    check("對外 IP", egress_ip)
    check("SSL 環境", ssl_mode)

    log("\n-- 櫃買 TPEx --")
    r1 = check("robots.txt", tpex_robots)
    r2 = check("靜態 CSV（不帶 cookie/referer）", tpex_static_bare)
    r3 = check("靜態 CSV 2007-01-02（歷史起點）", tpex_static_earliest)
    r4 = check("索引 API POST", tpex_index_post)
    check("索引 API POST（不帶 referer）", tpex_index_bare)
    r6 = check("解析器對真實檔案", tpex_parse)

    if skip_mops:
        log("\n-- MOPS --")
        log("        （已跳過：mopsov 的 robots.txt disallow /mops/，"
            "MOPS 只在本機跑，不在雲端跑）")
    else:
        log("\n-- MOPS --")
        check("可達性", mops_reachable)
        check("t120sg01 參數規則", mops_t120sg01)
        check("包裹下載 step=12", mops_bulk)

    log("\n-- 其他來源 --")
    check("集保 opendata 2-8", tdcc)
    check("cb168 output.xlsx", cb168)

    log("\n" + "=" * 62)
    log(" 判讀")
    log("=" * 62)
    if r2 and r3 and r6:
        log(" 櫃買回補可以開跑：")
        log("     python scripts/tpex_cb.py backfill")
        if not r4:
            log(" 索引 API 不通但靜態檔可抓 —— 改用日期推導網址，")
            log("     代價是要自行處理休市日（抓到 404 就跳過）。")
    elif r1 and not r2:
        log(" 連得上櫃買但抓不到靜態檔 —— 多半是限速或 UA 檢查。")
        log("     把 SLEEP_TPEX 調到 8~10 再試。")
    else:
        log(" 櫃買不通。若瀏覽器開得起 tpex.org.tw 而 Python 不行，")
        log("     通常是憑證或 Proxy 問題，把上面的錯誤訊息貼回來。")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"\n完整報告：{REPORT}")


if __name__ == "__main__":
    main()
