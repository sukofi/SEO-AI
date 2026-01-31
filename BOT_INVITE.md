# Discord Bot 招待手順

## Botがサーバーに表示されない場合

Botをサーバーに招待する必要があります。

### 招待URL生成手順

1. https://discord.com/developers/applications にアクセス
2. 作成したアプリケーション（SEO Reporter）を選択
3. 左メニューから **「OAuth2」** → **「URL Generator」** を選択
4. **SCOPES** で以下を選択：
   - ✅ `bot`
   - ✅ `applications.commands`

5. **BOT PERMISSIONS** で以下を選択：
   - ✅ Read Messages/View Channels
   - ✅ Send Messages
   - ✅ Send Messages in Threads
   - ✅ Embed Links
   - ✅ Attach Files
   - ✅ Read Message History
   - ✅ Use Slash Commands

6. 下部に生成された **GENERATED URL** をコピー

7. ブラウザで開いて、サーバーを選択して「認証」をクリック

### 確認方法

招待後、Discordサーバーのメンバーリストに **SEO Reporter** が表示されます。
オンライン状態（緑色）になっていれば成功です。

### トラブルシューティング

**Bot がオフライン表示の場合:**
- ターミナルでBotが起動中か確認
- ログに「Bot logged in as SEO Reporter#3405」が表示されているか確認

**コマンドが表示されない場合:**
- サーバーからBotを一度キックして再招待
- 数分待ってからDiscordを再起動
