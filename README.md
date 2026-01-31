# SEO Weekly Reporter

週1回のcron実行を想定したSEO監視スクリプトです。Googleスプレッドシートからキーワードを読み込み、SERP APIで順位を取得し、トップ10かつ順位下降のキーワードをGeminiで差分分析してDiscordへ通知します。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`.env.example` を `.env` にコピーして環境変数を設定してください。

## 実行

```bash
python src/seo_reporter.py
```

## cron設定例 (週1回)

```cron
# 毎週月曜9:00 (JST) に実行
0 9 * * 1 cd /workspace/SEO-AI && /usr/bin/env bash -lc 'source .venv/bin/activate && python src/seo_reporter.py >> logs/cron.log 2>&1'
```

## スプレッドシートの入力形式

`GOOGLE_SHEETS_RANGE` の範囲は以下の形式を想定しています。

| keyword | previous_rank |
| --- | --- |
| キーワード1 | 3 |
| キーワード2 | 5 |

`keyword` と `previous_rank` のヘッダー行がある場合は自動でスキップします。
