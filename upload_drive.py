# -*- coding: utf-8 -*-
"""MP3 と要約を Google ドライブの専用フォルダへ置く。

認証は clasp のトークン（~/.clasprc.json）。スコープは drive.file で、このアプリで作ったファイルしか触れない。
ほかの Drive のファイルには手が届かないので、G: の書き込みガードの趣旨とは干渉しない。
トークンの値は画面・ファイル・ログに出さない。共有設定は付けない（持ち主だけが見られる）。
使い方: python upload_drive.py runs/2026-10-29/ma_news_2026-10-29.mp3 [ほかのファイル...]

日付つきの ma_news_<日付>.mp3 / .md を置くと、あわせて ma_news_latest.mp3 / .md の中身も差し替える
（2026-11-04〜、携帯の「ホーム画面に追加」用）。latest は同じファイルIDのまま中身だけ更新する。
IDが変わるとホーム画面のショートカットが切れるため、初回だけ新規作成し、IDを drive.json の "latest" に保存する。
"""
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "drive.json")  # 作ったフォルダのID
FOLDER_NAME = "M&Aニュース音声"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def token():
    """手元は clasp のトークン。クラウドは環境変数 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN"""
    if os.environ.get("GOOGLE_REFRESH_TOKEN"):
        d = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
             "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"]}
    else:
        d = json.load(open(os.path.expanduser("~/.clasprc.json"), encoding="utf-8"))["tokens"]["default"]
    body = urllib.parse.urlencode({"client_id": d["client_id"], "client_secret": d["client_secret"],
                                   "refresh_token": d["refresh_token"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", body))["access_token"]


def call(method, url, tok, body=None, content_type="application/json"):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer " + tok, "Content-Type": content_type})
    return json.load(urllib.request.urlopen(req))


def folder_id(tok):
    cfg = load_config()
    if cfg.get("folderId"):
        return cfg["folderId"]
    r = call("POST", API + "?fields=id", tok, {"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"})
    cfg["folderId"] = r["id"]
    save_config(cfg)
    return r["id"]


def load_config():
    return json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


DATED = re.compile(r"^ma_news_\d{4}-\d{2}-\d{2}(\.(mp3|md))$")


def update_latest(path, parent, tok):
    """ma_news_latest.<拡張子> を同じIDのまま上書きする。無ければ作ってIDを保存する。"""
    m = DATED.match(os.path.basename(path))
    if not m:
        return None
    ext = m.group(1)
    name = "ma_news_latest" + ext
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    cfg = load_config()
    fid = cfg.get("latest", {}).get(ext)
    if fid:
        try:
            meta = call("GET", f"{API}/{fid}?fields=id,trashed", tok)
            if not meta.get("trashed"):
                return call("PATCH", f"{UPLOAD}/{fid}?uploadType=media&fields=id,webViewLink", tok, data, mime)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        # 消された・ゴミ箱にある：作り直す（ホーム画面のショートカットは作り直しが要る）
        sys.stdout.buffer.write(f"注意: {name}（ID {fid}）が見つからないため作り直します。携帯のショートカットも作り直しが要ります\n".encode("utf-8"))
    r = create(name, data, mime, parent, tok)
    cfg.setdefault("latest", {})[ext] = r["id"]
    save_config(cfg)
    return r


def create(name, data, mime, parent, tok):
    boundary = "ma_news_boundary"
    meta = json.dumps({"name": name, "parents": [parent]}, ensure_ascii=False)
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--".encode()
    return call("POST", f"{UPLOAD}?uploadType=multipart&fields=id,webViewLink", tok, body, f"multipart/related; boundary={boundary}")


def upload(path, parent, tok):
    name = os.path.basename(path)
    q = urllib.parse.quote(f"name='{name}' and '{parent}' in parents and trashed=false")
    old = call("GET", f"{API}?q={q}&fields=files(id)", tok)["files"]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    # 同じ名前があれば差し替える（再実行で増やさない）
    if old:
        return call("PATCH", f"{UPLOAD}/{old[0]['id']}?uploadType=media&fields=id,webViewLink", tok, data, mime)
    return create(name, data, mime, parent, tok)


def main():
    tok = token()
    parent = folder_id(tok)
    for p in sys.argv[1:]:
        r = upload(p, parent, tok)
        sys.stdout.buffer.write(f"{os.path.basename(p)} {r['webViewLink']}\n".encode("utf-8"))
        lr = update_latest(p, parent, tok)
        if lr:
            ext = os.path.splitext(p)[1]
            sys.stdout.buffer.write(f"ma_news_latest{ext} {lr['webViewLink']} (id {lr['id']})\n".encode("utf-8"))
    sys.stdout.buffer.write(f"folder https://drive.google.com/drive/folders/{parent}\n".encode("utf-8"))


if __name__ == "__main__":
    main()
