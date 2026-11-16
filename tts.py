# -*- coding: utf-8 -*-
"""読み上げ原稿（1行1段落のテキスト）を MP3 にする。既定は edge-tts（無料・Microsoft の Neural 音声）。

読み間違いは yomi.csv（表記,読み）で原稿の側を置き換えて直す。
使い方: python tts.py runs/2026-10-29/script.txt [--engine edge|sapi] [--rate +15%]
"""
import argparse
import asyncio
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = "ja-JP-NanamiNeural"
RATE = "+10%"  # 自転車で聞く前提で少し速め


def apply_yomi(text):
    path = os.path.join(HERE, "yomi.csv")
    if os.path.exists(path):
        for row in csv.reader(open(path, encoding="utf-8-sig")):
            if len(row) == 2 and row[0] and not row[0].startswith("#"):
                text = text.replace(row[0], row[1])
    return text


def _patch_edge_ssl():
    """クラウド環境はプロキシが TLS を中継するので、edge-tts が使う certifi の証明書だけでは
    検証に失敗する（2026-11-13 まで毎朝 AI がその場で回避していた）。
    certifi に加えて OS の証明書と、SSL_CERT_FILE／REQUESTS_CA_BUNDLE が指す証明書も信頼する。"""
    import ssl
    import edge_tts.communicate as comm
    ctx = comm._SSL_CTX if isinstance(getattr(comm, "_SSL_CTX", None), ssl.SSLContext) else ssl.create_default_context()
    try:
        ctx.load_default_certs()
    except Exception:
        pass
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and os.path.isfile(path):
            try:
                ctx.load_verify_locations(cafile=path)
            except Exception:
                pass
    for path in ("/root/.ccr/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if os.path.isfile(path):
            try:
                ctx.load_verify_locations(cafile=path)
            except Exception:
                pass
    comm._SSL_CTX = ctx


async def edge(text, out, rate):
    import edge_tts
    _patch_edge_ssl()
    await edge_tts.Communicate(text, VOICE, rate=rate).save(out)


def sapi(text, out):
    """Windows 標準の Haruka。WAV しか出ない（MP3 にするには ffmpeg が要る）"""
    wav = os.path.splitext(out)[0] + ".wav"
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          f"$s.SetOutputToWaveFile('{wav}');$s.Rate=1;$s.Speak([IO.File]::ReadAllText($env:TTS_TXT,[Text.Encoding]::UTF8));$s.Dispose()")
    tmp = out + ".txt"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, env={**os.environ, "TTS_TXT": tmp})
    finally:
        os.remove(tmp)
    return wav


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--engine", default="edge", choices=["edge", "sapi"])
    ap.add_argument("--rate", default=RATE)
    ap.add_argument("--out")
    a = ap.parse_args()
    text = apply_yomi(open(a.script, encoding="utf-8").read())
    out = a.out or os.path.splitext(a.script)[0] + ".mp3"
    if a.engine == "edge":
        asyncio.run(edge(text, out, a.rate))
    else:
        out = sapi(text, out)
    size = os.path.getsize(out)
    sys.stdout.buffer.write(f"{out} {size / 1024 / 1024:.1f}MB\n".encode("utf-8"))


if __name__ == "__main__":
    main()
