# Discord Bot Setup Guide

## 概要

SEO Reporter Discord Bot は、Discordから直接SEO分析を実行できるインタラクティブなbotです。

## 機能

### スラッシュコマンド

- `/rank [キーワード]` - 特定キーワードの現在順位を即座に取得
- `/analyze [キーワード]` - 詳細な競合分析（メトリクス比較 + AI分析）
- `/status` - Botのステータスと登録キーワード一覧を表示

### チャット機能

Botをメンション（`@SEO Reporter`）してメッセージを送ると、GeminiがSEOに関する質問に回答します。

## セットアップ

### 1. Discord Bot の作成

1. [Discord Developer Portal](https://discord.com/developers/applications) にアクセス
2. 「New Application」をクリック
3. アプリケーション名を入力（例: SEO Reporter）
4. 左メニューから「Bot」を選択
5. 「Add Bot」をクリック
6. 「Reset Token」をクリックしてトークンをコピー

### 2. Bot の権限設定

「OAuth2」→「URL Generator」で以下を選択：

**Scopes:**
- `bot`
- `applications.commands`

**Bot Permissions:**
- Read Messages/View Channels
- Send Messages
- Embed Links
- Use Slash Commands

生成されたURLでBotをサーバーに招待します。

### 3. 環境変数の設定

`.env` ファイルに以下を追加：

```
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
```

### 4. Bot の起動

```bash
.venv/bin/python src/discord_bot.py
```

## 使用例

### 順位確認
```
/rank 理学療法士 将来性
```

### 詳細分析
```
/analyze 理学療法士 将来性
```

### チャット
```
@SEO Reporter タイトルタグの最適な文字数は？
```

## 注意事項

- Botは24時間稼働させる必要があります（サーバーまたはクラウドで実行推奨）
- SERP API、Gemini API の使用量に注意してください
- 分析には数秒～数十秒かかる場合があります

## トラブルシューティング

### Bot がオンラインにならない
- `DISCORD_BOT_TOKEN` が正しく設定されているか確認
- Botの権限が正しく設定されているか確認

### コマンドが表示されない
- Bot起動時のログで「Synced X command(s)」を確認
- サーバーからBotを一度キックして再招待

### 分析が失敗する
- `.env` の各API設定を確認
- ログファイル（`logs/seo_reporter.log`）を確認
