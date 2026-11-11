# クラウド実行（2026-11-11〜準備）

PC が7時に起きていない日でも7時に出すため、Claude Code のクラウドの定期実行（ルーチン）で動かす。
手元の定期実行（`ma-news-daily`）は、クラウドで1回通ったのを確かめてから止める。両方を同時に動かさない。

## 手元とクラウドの違い
| | 手元 | クラウド |
|---|---|---|
| ウォッチ先 | `watchlist.csv`（社名つき） | `watchlist_codes.csv`（証券コードだけ）。社名は公開の EDINET コードリストから引く |
| EDINET キー | `.env` | 環境変数 `EDINET_API_KEY` |
| Drive の鍵 | `~/.clasprc.json` | 環境変数 `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` |
| 前回の続き・辞書・ログ | フォルダの中 | Drive の `M&Aニュース音声/_state`。`sync_state.py pull` で取ってきて `push` で戻す |

- ロングリストの社名・テーマ・優先度、`build_watchlist.py`（パスワード入り）は GitHub に置かない（`.gitignore`）
- ロングリストの版が変わったら、手元で `python build_watchlist.py` を流し、`watchlist_codes.csv` だけをコミットする
- ロングリストの表記がEDINETの提出者名と違う3社は、クラウドではほかの会社の開示のタイトルに出る名前で当てられない（提出者名では当たる）

## クラウドの環境
- セットアップスクリプト：
  ```
  apt-get update && apt-get install -y poppler-utils
  pip install -r requirements.txt
  ```
- 環境変数：`EDINET_API_KEY`、`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REFRESH_TOKEN`、`TZ=Asia/Tokyo`
- 外への通信を許すドメイン：`www.release.tdnet.info`、`api.edinet-fsa.go.jp`、`disclosure2.edinet-fsa.go.jp`、`disclosure2dl.edinet-fsa.go.jp`、`oauth2.googleapis.com`、`www.googleapis.com`、`speech.platform.bing.com`（edge-tts）、pypi
- 時刻：平日 7:00 JST ＝ cron `0 22 * * 0-4`（UTC）

## ルーチンのプロンプト
```
あなたは山田さんの M&Aニュース音声エージェントです。返答・ログはすべて日本語で書いてください。
今日の日付は `TZ=Asia/Tokyo date +%F` で決めてください（クラウドは UTC で動くため）。

1. `python sync_state.py pull` で前回の続き・辞書・今日のログを Drive から取ってくる
2. `RUNBOOK.md` と `.claude/CLAUDE.md` を読み、RUNBOOK の手順どおりに1回分だけ実行する
3. 終わり方（done・skipped-holiday・failed）に関わらず、最後に必ず `python sync_state.py push` を流す
   （RUNBOOK の手順1で「本日分は済み」で終える場合は不要）

ウォッチ先は watchlist_codes.csv（証券コードだけ）を使う。社名の一覧はこの環境に無いが、それで正しい。
環境変数の鍵・トークンを画面・ログ・ファイルに出さない。GitHub に push しない。
```
