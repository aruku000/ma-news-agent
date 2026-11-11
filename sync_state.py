# -*- coding: utf-8 -*-
"""クラウド実行のための引き継ぎ。前回の続き（state.json）、読みの辞書（yomi.csv）、当日のログを
Drive の「M&Aニュース音声/_state」に置き、実行の最初に取ってきて最後に戻す。

クラウドの作業場所は毎回まっさらなので、これが無いと前回の続きと二重起動の判定ができない。
認証・スコープは upload_drive.py と同じ（drive.file。共有設定は付けない）。
使い方:
  python sync_state.py pull   Drive → 手元（Drive に無いものはそのまま）
  python sync_state.py push   手元 → Drive
"""
import datetime as dt
import os
import sys
import urllib.parse
import urllib.request

import upload_drive as ud

HERE = os.path.dirname(os.path.abspath(__file__))
SUB = "_state"
JST = dt.timezone(dt.timedelta(hours=9))


def files():
    today = dt.datetime.now(JST).date().isoformat()
    return {"state.json": "state.json", "yomi.csv": "yomi.csv", f"log_{today}.md": os.path.join("logs", f"{today}.md")}


def find(name, parent, tok, folder=False):
    q = f"name='{name}' and '{parent}' in parents and trashed=false"
    if folder:
        q += " and mimeType='application/vnd.google-apps.folder'"
    r = ud.call("GET", f"{ud.API}?q={urllib.parse.quote(q)}&fields=files(id)", tok)["files"]
    return r[0]["id"] if r else None


def sub_id(tok):
    parent = ud.folder_id(tok)
    fid = find(SUB, parent, tok, folder=True)
    if not fid:
        fid = ud.call("POST", ud.API + "?fields=id", tok,
                      {"name": SUB, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]})["id"]
    return fid


def pull(tok, sub):
    for remote, local in files().items():
        fid = find(remote, sub, tok)
        if not fid:
            print(f"{remote}: Drive に無い（手元のまま）")
            continue
        req = urllib.request.Request(f"{ud.API}/{fid}?alt=media", headers={"Authorization": "Bearer " + tok})
        data = urllib.request.urlopen(req).read()
        path = os.path.join(HERE, local)
        os.makedirs(os.path.dirname(path) or HERE, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print(f"{remote}: 取得 {len(data)} bytes")


def push(tok, sub):
    for remote, local in files().items():
        path = os.path.join(HERE, local)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        mime = "text/plain" if not remote.endswith(".json") else "application/json"
        fid = find(remote, sub, tok)
        if fid:
            ud.call("PATCH", f"{ud.UPLOAD}/{fid}?uploadType=media&fields=id", tok, data, mime)
        else:
            ud.create(remote, data, mime, sub, tok)
        print(f"{remote}: 保存 {len(data)} bytes")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("pull", "push"):
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    tok = ud.token()
    sub = sub_id(tok)
    (pull if sys.argv[1] == "pull" else push)(tok, sub)


if __name__ == "__main__":
    main()
