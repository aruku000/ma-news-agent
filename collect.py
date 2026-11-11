# -*- coding: utf-8 -*-
"""TDnet と EDINET から当日分の開示を集め、対象を選んで runs/<日付>/items.json に書く。

対象① watch : watchlist.csv の会社の資本政策イベント
対象② deal  : 全上場企業のM&A案件（規模・SaaS/HRテックの判定は要約の段階で本文を読んで行う）
対象③ theme : ウォッチ先以外の、下の THEMES の領域に関係しそうな会社の開示。
              タイトルはほとんど事業を語らないので、資本イベントの開示は本文の語でふるいにかける。
              事業の本当の当否は要約の段階で本文を読んで決める
              主要株主の異動のように発行会社の事業を本文が語らない開示があるので、
              語で落ちた分も items.json の screened_out に会社名とタイトルを残し、Claude が社名で見直す
PDF は本文のテキストだけ runs/<日付>/txt/ に残す。

使い方:
  python collect.py                 前回の続き〜今日
  python collect.py --from 2026-10-29 --to 2026-10-29   期間を指定（state は更新しない）
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import zipfile

import requests
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
ENV = os.path.join(HERE, ".env")
TDNET = "https://www.release.tdnet.info/inbs/"
EDINET = "https://api.edinet-fsa.go.jp/api/v2/"
UA = {"User-Agent": "Mozilla/5.0 (personal disclosure digest)"}
JST = dt.timezone(dt.timedelta(hours=9))
WAIT = 1.0  # 取得の間隔（秒）
MAX_PDF = 60  # 1回に本文を読む件数の上限。超えたら本文なしで残し、警告に出す
SCAN_CHARS = 6000  # 本文の語を見る範囲。事業内容は冒頭に出る。長い開示の後ろの定型文で当たるのを避ける

# 対象①：ウォッチ先の資本政策。タイトルの語で拾う
WATCH_WORDS = [
    "公開買付", "大量保有", "MBO", "マネジメント・バイアウト", "マネジメントバイアウト", "資本業務提携", "資本提携", "業務提携",
    "第三者割当", "株式交換", "株式移転", "株式交付", "株式分配", "合併", "会社分割", "事業譲渡", "事業の譲", "株式譲渡", "株式の譲渡",
    "子会社化", "子会社の異動", "主要株主", "親会社", "筆頭株主", "支配株主", "上場廃止", "非公開化",
    "株式併合", "特別支配株主", "売渡請求", "株主提案", "買収", "防衛策", "経営統合", "自己株式の取得", "自己株式取得",
    "代表取締役の異動", "代表者の異動", "中期経営計画", "増資", "新株予約権の発行", "行使価額修正",
]
# 対象②：全社から拾うM&Aの語
DEAL_WORDS = [
    "公開買付", "MBO", "マネジメント・バイアウト", "マネジメントバイアウト", "資本業務提携", "資本提携", "株式交換", "株式移転", "株式交付",
    "合併", "吸収分割", "事業譲渡", "事業の譲", "事業の承継", "子会社化", "子会社の異動", "買収", "経営統合", "非公開化", "売渡請求",
    "株式の取得", "株式取得", "持分の取得",
]
# 落とす語。定例の報告・役員向け株式報酬など
NOISE = [
    "取得状況", "譲渡制限付株式", "ストックオプション", "株式報酬", "役員報酬", "取得結果", "終了に関するお知らせ",
    "投資法人", "インフラファンド", "従業員持株", "株式給付", "ESOP",
]
# 対象②だけで落とす語。自社株買いと、グループ内の再編は案件として数えない
DEAL_NOISE = ["自己株式", "完全子会社の吸収合併", "連結子会社の吸収合併", "子会社の吸収合併", "子会社との会社分割", "子会社との吸収"]
# 対象③：領域ごとの語。ざっくりでよい。厳密な基準は持たず、当否は Claude が本文で決める
THEMES = {
    "セキュリティ": ["セキュリティ", "脆弱性診断", "ペネトレーションテスト", "ゼロトラスト", "SOC"],
    "DX・SES": ["システムエンジニアリングサービス", "SES", "DXコンサル", "DX支援", "ITコンサル", "受託開発", "システム開発", "システムインテグレーション", "SIer", "エンジニア派遣"],
    "SMB金融": ["ファクタリング", "BNPL", "後払い", "決済代行", "売掛債権", "請求書カード払い", "ビジネスローン", "キャッシュレス決済", "決済サービス"],
    "リアル産業DX": ["BIM", "CIM", "CAD", "CAE", "建設DX", "不動産DX", "不動産テック", "建設テック", "施工管理", "設計支援", "設計DX", "不動産査定"],
}
# ASCII の語は英単語の一部で当たらないようにする。日本語は \w に入るので \b は使えない
THEME_RE = {k: re.compile("|".join(f"(?<![A-Za-z]){w}(?![A-Za-z])" if w.isascii() else w for w in v)) for k, v in THEMES.items()}
# タイトルで見るとき落とす語。被害の報告は事業の話ではない
THEME_NOISE = ["サイバー攻撃", "不正アクセス", "情報漏", "ランサムウェア", "システム障害"]
# 対象③：本文を見る資本イベント。対象②の語に当たらなかった分
THEME_EVENTS = [
    "資本業務提携", "資本提携", "業務提携", "第三者割当", "主要株主", "親会社", "筆頭株主", "支配株主", "上場廃止",
    "株式譲渡", "株式の譲渡", "事業譲渡", "事業の譲", "行使価額修正", "新株予約権の発行",
]
# 同じイベントの定例・経過の開示は本文を見ない
THEME_EVENT_NOISE = ["自己株式", "行使状況", "月間行使", "大量行使", "行使完了", "払込完了", "支配株主等に関する事項", "親会社等の決算", "訂正"]
# EDINET の書類種別（API仕様書の docTypeCode）
EDINET_TYPES = {
    "180": "臨時報告書", "240": "公開買付届出書", "250": "訂正公開買付届出書", "260": "公開買付撤回届出書",
    "270": "公開買付報告書", "290": "意見表明報告書", "350": "大量保有報告書",
}
EDINET_ALL = {"240", "260", "290"}  # ウォッチ先でなくても案件として拾う
KENPO = re.compile(r"合併|株式交換|株式移転|会社分割|事業譲渡|特定子会社|親会社|主要株主")  # 臨時報告書で見る提出理由


def load_env():
    if os.path.exists(ENV):
        for line in open(ENV, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


CODELIST = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"


def load_watch():
    """手元では watchlist.csv（社名つき）を読む。無ければ（クラウド）watchlist_codes.csv の証券コードだけを読み、
    EDINET コードと提出者名は公開のコードリストから引く。社名・テーマ・優先度はクラウドに持ち出さない"""
    full = os.path.join(HERE, "watchlist.csv")
    if os.path.exists(full):
        rows = list(csv.DictReader(open(full, encoding="utf-8-sig")))
    else:
        codes = {r["code"] for r in csv.DictReader(open(os.path.join(HERE, "watchlist_codes.csv"), encoding="utf-8-sig"))}
        raw = requests.get(CODELIST, headers=UA, timeout=60).content
        z = zipfile.ZipFile(io.BytesIO(raw))
        lines = list(csv.reader(io.StringIO(z.read(z.namelist()[0]).decode("cp932"))))
        head = lines[1]
        i_e, i_n, i_c = head.index("ＥＤＩＮＥＴコード"), head.index("提出者名"), head.index("証券コード")
        rows = [{"code": r[i_c], "edinet_code": r[i_e], "edinet_name": r[i_n], "longlist_name": ""}
                for r in lines[2:] if r[i_c].strip() in codes]
        if len(rows) < len(codes):
            print(f"注意: ウォッチ先 {len(codes)} 社のうち {len(codes) - len(rows)} 社がコードリストに無い")
    return {r["code"]: r for r in rows}, {r["edinet_code"]: r for r in rows}


def watch_name(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"株式会社|\(株\)|㈱|\s|・", "", s).upper()


def watch_names(by_code):
    """タイトルの中のウォッチ先の社名を探す正規表現。ほかの会社の開示（買い集め・TOB）で名前が出る分を拾う。
    「セキュア」が「セキュアな」に当たらないよう、前後がカタカナ・英数字のときは当てない"""
    pats = []
    for r in by_code.values():
        for n in {watch_name(r["edinet_name"]), watch_name(r["longlist_name"])}:
            if len(n) >= 3:
                pats.append((re.compile(f"(?<![ァ-ヶーA-Z0-9]){re.escape(n)}(?![ァ-ヶーA-Z0-9])"), r))
    return pats


def watch_in_title(title, pats):
    t = watch_name(title)
    return next((r for p, r in pats if p.search(t)), None)


def tdnet_day(day):
    """1日分の適時開示を全ページ読む。一覧は1ページ100件で、31日より前は消える"""
    ymd = day.strftime("%Y%m%d")
    out, page = [], 1
    while True:
        r = requests.get(f"{TDNET}I_list_{page:03d}_{ymd}.html", headers=UA, timeout=30)
        if r.status_code == 404:
            break
        r.raise_for_status()
        s = BeautifulSoup(r.content, "lxml", from_encoding="utf-8")
        rows = s.select("#main-list-table tr")
        for tr in rows:
            a = tr.select_one(".kjTitle a")
            if not a:
                continue
            out.append({
                "id": "T" + a["href"].removesuffix(".pdf"), "source": "TDnet", "date": day.isoformat(),
                "time": tr.select_one(".kjTime").get_text(strip=True), "code": tr.select_one(".kjCode").get_text(strip=True),
                "company": tr.select_one(".kjName").get_text(strip=True),
                "title": unicodedata.normalize("NFKC", a.get_text(strip=True)), "url": urllib.parse.urljoin(TDNET, a["href"]),
            })
        if len(rows) < 100:
            break
        page += 1
        time.sleep(WAIT)
    return out


def edinet_day(day, key):
    r = requests.get(EDINET + "documents.json", params={"date": day.isoformat(), "type": 2, "Subscription-Key": key}, headers=UA, timeout=60)
    r.raise_for_status()
    j = r.json()
    if str(j.get("metadata", {}).get("status", j.get("StatusCode"))) != "200":
        raise RuntimeError(f"EDINET: {j}")
    out = []
    for d in j["results"]:
        if d.get("docTypeCode") not in EDINET_TYPES or d.get("withdrawalStatus") == "1":
            continue
        out.append({
            "id": "E" + d["docID"], "source": "EDINET", "date": day.isoformat(), "time": (d.get("submitDateTime") or "")[-5:],
            "code": d.get("secCode") or "", "company": d.get("filerName") or "",
            "title": d.get("docDescription") or EDINET_TYPES[d["docTypeCode"]],
            "url": f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{d['docID']}", "doc_type": d["docTypeCode"], "doc_id": d["docID"],
            "subject_edinet": [x for x in (d.get("issuerEdinetCode"), d.get("subjectEdinetCode"), d.get("edinetCode")) if x],
            "reason": d.get("currentReportReason") or "",
        })
    return out


def has(words, title):
    return any(w in title for w in words)


def theme_hits(text):
    """当たった領域と語を返す。PDF の折り返しで語が切れるので空白を詰めて見る"""
    text = re.sub(r"\s", "", unicodedata.normalize("NFKC", text))
    hits = {k: sorted(set(r.findall(text))) for k, r in THEME_RE.items()}
    return {k: v for k, v in hits.items() if v}


def select_tdnet(items, by_code):
    pats = watch_names(by_code)
    out = []
    for it in items:
        t = it["title"]
        if has(NOISE, t):
            continue
        own = by_code.get(it["code"])
        named = None if own else watch_in_title(t, pats)
        hit = own or named
        # ほかの会社の開示にウォッチ先の名前が出るのは、買い集め・TOB・提携などがほとんどなので語を問わない
        if (own and has(WATCH_WORDS, t)) or named:
            it.update(target="watch", watch=hit)
        elif has(DEAL_WORDS, t) and not has(DEAL_NOISE, t):
            it.update(target="deal")
        elif hit:
            continue
        elif theme_hits(t) and not has(THEME_NOISE, t):
            it.update(target="theme", theme=theme_hits(t), hit_in="タイトル")
        elif has(THEME_EVENTS, t) and not has(THEME_EVENT_NOISE, t):
            it.update(target="screen")  # 本文の語で theme にするか落とすか決める
        else:
            continue
        out.append(it)
    return out


def select_edinet(items, by_edinet):
    out = []
    for it in items:
        hit = next((by_edinet[e] for e in it["subject_edinet"] if e in by_edinet), None)
        reason_ok = it["doc_type"] != "180" or KENPO.search(it["reason"] + it["title"])
        if hit and reason_ok:
            it.update(target="watch", watch=hit)
        elif it["doc_type"] in EDINET_ALL:
            it.update(target="deal")
        else:
            continue
        out.append(it)
    return out


def pdf_text(pdf_bytes, path):
    tmp = path + ".pdf"
    with open(tmp, "wb") as f:
        f.write(pdf_bytes)
    try:
        subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", tmp, path], check=True, capture_output=True, timeout=60)
    finally:
        os.remove(tmp)


def zip_text(zip_bytes, path):
    """EDINET の PDF は pdftotext で文字が取れないので、提出本文の HTML（PublicDoc）から取る"""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = sorted(n for n in z.namelist() if "PublicDoc" in n and n.endswith((".htm", ".html")))
    text = "\n".join(BeautifulSoup(z.read(n), "lxml").get_text("\n", strip=True) for n in names)
    if not text.strip():
        raise RuntimeError("本文が空")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def fetch_body(it, txtdir, key):
    path = os.path.join(txtdir, it["id"] + ".txt")
    if it["source"] == "TDnet":
        r = requests.get(it["url"], headers=UA, timeout=60)
        r.raise_for_status()
        pdf_text(r.content, path)
    else:
        r = requests.get(f"{EDINET}documents/{it['doc_id']}", params={"type": 1, "Subscription-Key": key}, headers=UA, timeout=120)
        r.raise_for_status()
        zip_text(r.content, path)
    it["txt"] = os.path.relpath(path, HERE).replace("\\", "/")


def screen_themes(picked):
    """本文の語で対象③を決める。対象②にも印を付け、小さい案件を要約の段階で拾えるようにする"""
    out, rest = [], []
    for it in picked:
        if it["target"] in ("deal", "screen"):
            body = ""
            if it.get("txt"):
                body = open(os.path.join(HERE, it["txt"]), encoding="utf-8", errors="ignore").read()[:SCAN_CHARS]
            hits = theme_hits(it["title"] + "\n" + body)
            if hits:
                it.update(theme=hits, hit_in="タイトルか本文")
                if it["target"] == "screen":
                    it["target"] = "theme"
        (rest if it["target"] == "screen" else out).append(it)
    return out, rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--no-body", action="store_true", help="PDF本文を取らない")
    a = ap.parse_args()
    load_env()
    key = os.environ.get("EDINET_API_KEY", "")
    today = dt.datetime.now(JST).date()  # クラウドは UTC で動くので日本時間で決める
    manual = bool(a.start)
    state = {"last_date": (today - dt.timedelta(days=1)).isoformat(), "seen": []}
    if os.path.exists(STATE):
        state = json.load(open(STATE, encoding="utf-8"))
    start = dt.date.fromisoformat(a.start or state["last_date"])
    end = dt.date.fromisoformat(a.end or today.isoformat())
    seen = set(state["seen"])
    by_code, by_edinet = load_watch()

    picked, counts, warn = [], {}, []
    day = start
    while day <= end:
        td = tdnet_day(day)
        p = select_tdnet(td, by_code)
        ed = []
        if key:
            try:
                ed = edinet_day(day, key)
                p += select_edinet(ed, by_edinet)
            except Exception as e:  # EDINET が落ちていても TDnet の分は出す
                warn.append(f"EDINET {day}: {e}".replace(key, "***"))
        else:
            warn.append("EDINET_API_KEY が無いので EDINET は読んでいない")
        counts[day.isoformat()] = {"tdnet": len(td), "edinet": len(ed)}
        picked += [it for it in p if manual or it["id"] not in seen]
        day += dt.timedelta(days=1)
        time.sleep(WAIT)

    # ウォッチ先を先に本文取得する。本文を見てふるう分は上限にかかったら捨ててよいので最後
    order = {"watch": 0, "deal": 1, "theme": 2, "screen": 3}
    picked.sort(key=lambda x: (order[x["target"]], x["date"], x["time"]))
    run = os.path.join(HERE, "runs", end.isoformat())
    txtdir = os.path.join(run, "txt")
    os.makedirs(txtdir, exist_ok=True)
    if not a.no_body:
        for i, it in enumerate(picked):
            if i >= MAX_PDF:
                warn.append(f"本文の上限 {MAX_PDF} 件を超えた分（{len(picked) - MAX_PDF}件）はタイトルだけ")
                break
            try:
                fetch_body(it, txtdir, key)
            except Exception as e:
                warn.append(f"本文取得失敗 {it['id']}: {e}".replace(key or "\0", "***"))
            time.sleep(WAIT)
    checked = [it["id"] for it in picked]
    picked, rest = screen_themes(picked)

    result = {"from": start.isoformat(), "to": end.isoformat(), "counts": counts, "warnings": sorted(set(warn)), "items": picked,
              "screened_out": rest}
    with open(os.path.join(run, "items.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    if not manual:
        keep = (state["seen"] + checked)[-3000:]
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"last_date": end.isoformat(), "seen": keep}, f, ensure_ascii=False)
    n = sum(1 for x in picked if x["target"] == "watch")
    d = sum(1 for x in picked if x["target"] == "deal")
    dt_hit = sum(1 for x in picked if x["target"] == "deal" and x.get("theme"))
    th = sum(1 for x in picked if x["target"] == "theme")
    print(f"{start}〜{end}：一覧 {counts}、ウォッチ先 {n} 件、案件候補 {d} 件（うち領域の語あり {dt_hit} 件）、"
          f"領域 {th} 件（ほかに語で落ちた {len(rest)} 件） → {run}")
    for w in result["warnings"]:
        print("注意:", w)


if __name__ == "__main__":
    main()
