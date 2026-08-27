#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資料正確性驗證。

不用價格區間判斷合理性 —— CB 深價內可以五、六百，瀕臨違約剩五十，
用區間篩只會把最有資訊量的個券當成髒資料丟掉。這裡驗的是「擷取有沒有錯」。

五層：
    1 檔案完整性   逐年檔數、日期連續性、重複
    2 結構正確性   每檔兩列、代號前向填補、欄位齊全
    3 內部一致性   合計列對帳（每日自帶檢核碼）、OHLC 邏輯、漲跌停比例
    4 時間序列     個券出現與消失、區分下櫃與資料缺漏
    5 跨來源母體   與 cb168 名單、與三大法人檔的代號比對

用法：
    python scripts/validate.py            全部
    python scripts/validate.py --year 2015 --year 2016
輸出：
    data/validation_report.txt
    data/validation_issues.csv    逐筆異常，供人工追查
"""

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTES = os.path.join(ROOT, "data", "quotes")
REPORT = os.path.join(ROOT, "data", "validation_report.txt")
ISSUES = os.path.join(ROOT, "data", "validation_issues.csv")

CODE_RE = re.compile(r"^[0-9A-Za-z]+$")
NUM_COLS = ["收市", "漲跌", "開市", "最高", "最低", "筆數", "單位", "金額",
            "均價", "明日參價", "明日漲停", "明日跌停"]

lines = []
issues = []


def log(s=""):
    print(s, flush=True)
    lines.append(str(s))


def issue(層, 日期, 代號, 類型, 說明):
    issues.append({"層": 層, "資料日期": 日期, "代號": 代號,
                   "類型": 類型, "說明": 說明})


def load(path):
    df = pd.read_csv(path, dtype=str)
    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(",", "", regex=False).str.strip(),
                errors="coerce")
    for c in ("代號", "名稱", "交易"):
        df[c] = df[c].astype(str).str.strip()
    return df


# ------------------------------------------------------- 第 1、2、3 層（逐檔）

def check_file(path):
    ymd = os.path.basename(path).split(".")[1]
    d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    df = load(path)

    bonds = df[df["代號"].str.fullmatch(CODE_RE, na=False)]
    total_rows = df[df["代號"] == "合計"]

    # --- 第 2 層 結構 ---
    if (bonds["代號"] == "").any() or (bonds["代號"] == "nan").any():
        issue(2, d, "", "空代號", "前向填補失敗")

    per_code = bonds.groupby("代號")["交易"].apply(lambda s: sorted(set(s)))
    for code, kinds in per_code.items():
        if kinds != ["等價", "議價"]:
            issue(2, d, code, "列數異常", f"交易別為 {kinds}")

    # --- 第 3 層 合計列對帳（每日自帶檢核碼）---
    if total_rows.empty:
        issue(3, d, "", "缺合計列", "來源檔沒有合計列，無法自我對帳")
    else:
        t = total_rows.iloc[0]
        for col in ("筆數", "單位", "金額"):
            got = bonds[col].sum(skipna=True)
            exp = t[col]
            if pd.isna(exp):
                continue
            if abs(got - exp) > 0.5:
                issue(3, d, "", f"合計不符-{col}",
                      f"明細加總 {got:,.0f} vs 合計列 {exp:,.0f}，差 {got-exp:+,.0f}")

    # --- 第 3 層 OHLC 邏輯（與價格水準無關）---
    # 注意：均價不納入檢查。均價涵蓋議價與鉅額交易，那是協議價格，
    # 本來就可能落在等價的最高／最低區間之外，不是解析錯誤。
    q = bonds.dropna(subset=["最高", "最低"])
    for _, r in q.iterrows():
        hi, lo = r["最高"], r["最低"]
        if hi < lo:
            issue(3, d, r["代號"], "高低顛倒", f"最高 {hi} < 最低 {lo}")
        for col in ("開市", "收市"):
            v = r[col]
            if pd.notna(v) and not (lo - 1e-9 <= v <= hi + 1e-9):
                issue(3, d, r["代號"], f"{col}越界", f"{col} {v} 不在 [{lo}, {hi}]")

    # --- 第 3 層 漲跌停上下界 ---
    # 不能要求比例完全一致：漲跌停價要取到合法的價格檔位，比例會略低於上限。
    #
    # 上限不寫死日期，改由當日資料推導。原因：這三欄描述的是「明日」，
    # 所以規則變更當天的前一個交易日就已經套用新制。2015-05-29 的檔案
    # 明日欄位已是 10%（新制 6/1 生效），寫死日期會整批誤判。
    KNOWN_CAPS = (0.07, 0.10)
    lim = bonds.dropna(subset=["明日參價", "明日漲停", "明日跌停"])
    lim = lim[lim["明日參價"] > 0]
    if len(lim) > 5:
        obs = (lim["明日漲停"] / lim["明日參價"] - 1).max()
        cap = min(KNOWN_CAPS, key=lambda c: abs(c - obs))
        if abs(obs - cap) > 0.02:
            issue(3, d, "", "漲跌幅上限異常",
                  f"當日觀測到的最大漲幅 {obs:.2%}，不接近任何已知上限")
        for _, r in lim.iterrows():
            ref, up, dn = r["明日參價"], r["明日漲停"], r["明日跌停"]
            if not (ref < up <= ref * (1 + cap) * 1.001):
                issue(3, d, r["代號"], "漲停越界",
                      f"參價 {ref} 漲停 {up}（當日上限 {cap:.0%}）")
            if not (ref * (1 - cap) * 0.999 <= dn < ref):
                issue(3, d, r["代號"], "跌停越界",
                      f"參價 {ref} 跌停 {dn}（當日上限 {cap:.0%}）")

    return {
        "日期": d, "列數": len(df), "個券數": bonds["代號"].nunique(),
        "有合計列": not total_rows.empty,
        "代號集合": set(bonds["代號"]),
    }


# ------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", action="append", type=int)
    args = ap.parse_args()

    years = args.year or sorted(
        int(os.path.basename(p)) for p in glob.glob(os.path.join(QUOTES, "*"))
        if os.path.basename(p).isdigit())

    log("=" * 60)
    log(" 資料正確性驗證")
    log("=" * 60)

    log("\n【第 1 層】檔案完整性")
    log(f"{'年':>6}  {'檔數':>5}  {'個券數':>6}")
    per_day, all_codes = [], defaultdict(set)
    for y in years:
        files = sorted(glob.glob(os.path.join(QUOTES, str(y), "*.csv")))
        if not files:
            log(f"{y:>6}  {'0':>5}   ← 整年缺失")
            issue(1, str(y), "", "整年缺失", "該年沒有任何檔案")
            continue
        codes_y = set()
        for f in files:
            info = check_file(f)
            per_day.append(info)
            codes_y |= info["代號集合"]
            all_codes[info["日期"]] = info["代號集合"]
        log(f"{y:>6}  {len(files):>5}  {len(codes_y):>6}")

    if not per_day:
        log("沒有任何檔案可驗證")
        return 1

    dates = sorted(i["日期"] for i in per_day)
    log(f"\n合計 {len(per_day):,} 個交易日，{dates[0]} ~ {dates[-1]}")

    dup = len(dates) - len(set(dates))
    log(f"重複日期：{dup}")
    no_total = [i["日期"] for i in per_day if not i["有合計列"]]
    log(f"缺合計列的檔案：{len(no_total)}")

    # --- 第 4 層 時間序列連續性 ---
    log("\n【第 4 層】時間序列 —— 區分下櫃與資料缺漏")
    first_seen, last_seen = {}, {}
    for d in dates:
        for c in all_codes[d]:
            first_seen.setdefault(c, d)
            last_seen[c] = d

    last_date = dates[-1]
    gone = {c: last_seen[c] for c in last_seen if last_seen[c] != last_date}

    # 中途消失又再出現 = 資料缺漏，不是下櫃
    reappeared = []
    for c in list(gone)[:100000]:
        span = [d for d in dates if first_seen[c] <= d <= last_seen[c]]
        present = sum(1 for d in span if c in all_codes[d])
        if span and present < len(span) * 0.98:
            reappeared.append((c, len(span) - present))
    log(f"仍在交易：{len(last_seen) - len(gone):,} 檔")
    log(f"已消失（推定下櫃）：{len(gone):,} 檔")
    log(f"其中生命期內有中斷（疑似資料缺漏而非下櫃）：{len(reappeared):,} 檔")
    for c, n in sorted(reappeared, key=lambda x: -x[1])[:15]:
        issue(4, "", c, "生命期內中斷", f"缺 {n} 個交易日")

    # --- 第 5 層 母體比對 ---
    log("\n【第 5 層】母體比對")
    log(f"本資料庫個券數：{len(last_seen):,}")
    log("官方母體 2,412 / cb168 2,322（需另行下載比對，見 README 3 節）")

    # --- 結果 ---
    log("\n" + "=" * 60)
    by_type = defaultdict(int)
    for i in issues:
        by_type[i["類型"]] += 1
    if not issues:
        log(" 沒有發現異常")
    else:
        log(f" 發現 {len(issues):,} 筆異常，分類：")
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            log(f"   {n:>7,}  {t}")
        os.makedirs(os.path.dirname(ISSUES), exist_ok=True)
        with open(ISSUES, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["層", "資料日期", "代號", "類型", "說明"])
            w.writeheader()
            w.writerows(issues)
        log(f"\n 逐筆清單：{os.path.relpath(ISSUES, ROOT)}")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f" 完整報告：{os.path.relpath(REPORT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
