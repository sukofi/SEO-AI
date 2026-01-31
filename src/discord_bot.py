import logging
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from typing import Optional

from config import Config
from seo_reporter import (
    fetch_serp,
    analyze_page_content,
    build_gemini_prompt,
    request_gemini,
    setup_logging,
    load_keywords,
    SerpResult,
    ContentMetrics
)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
config = Config.from_env()

# Conversation context storage (per user)
user_context = {}

class AnalysisContext:
    def __init__(self, keyword, rank, own_url, own_metrics, competitor_url, competitor_metrics, gaps):
        self.keyword = keyword
        self.rank = rank
        self.own_url = own_url
        self.own_metrics = own_metrics
        self.competitor_url = competitor_url
        self.competitor_metrics = competitor_metrics
        self.gaps = gaps
        self.timestamp = asyncio.get_event_loop().time()

@bot.event
async def on_ready():
    logging.info(f"Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="rank", description="特定キーワードの現在順位を取得")
@app_commands.describe(keyword="検索順位を調べたいキーワード")
async def rank_command(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()
    
    try:
        # Fetch SERP (run in thread pool to avoid blocking)
        serp_result = await asyncio.to_thread(fetch_serp, config, keyword)
        
        # Build response
        if serp_result.rank:
            embed = discord.Embed(
                title=f"🔍 順位確認: {keyword}",
                color=discord.Color.blue()
            )
            embed.add_field(name="現在順位", value=f"**{serp_result.rank}位**", inline=False)
            if serp_result.own_url:
                embed.add_field(name="URL", value=serp_result.own_url, inline=False)
        else:
            embed = discord.Embed(
                title=f"🔍 順位確認: {keyword}",
                description=f"❌ `{config.own_domain}` は検索結果トップ10に見つかりませんでした",
                color=discord.Color.red()
            )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logging.error(f"Error in rank command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.tree.command(name="analyze", description="キーワードの詳細な競合分析を実行")
@app_commands.describe(keyword="分析したいキーワード")
async def analyze_command(interaction: discord.Interaction, keyword: str):
    await interaction.response.defer()
    
    try:
        # Fetch SERP (run in thread pool)
        serp_result = await asyncio.to_thread(fetch_serp, config, keyword)
        
        if not serp_result.rank or not serp_result.own_url:
            await interaction.followup.send(f"❌ `{keyword}` で自社サイトが見つかりませんでした")
            return
        
        # Analyze content (run in thread pool)
        own_metrics = await asyncio.to_thread(analyze_page_content, serp_result.own_url, config.own_domain)
        
        # Find competitor one rank above
        target_pos = serp_result.rank - 1
        competitor = None
        for comp in serp_result.competitors:
            if comp.get("position") == target_pos:
                competitor = comp
                break
        
        if not competitor and serp_result.competitors:
            competitor = serp_result.competitors[0]
        
        competitor_metrics = None
        if competitor:
            comp_url = competitor.get("url")
            if comp_url:
                competitor_metrics = await asyncio.to_thread(analyze_page_content, comp_url)
        
        # Build embed
        embed = discord.Embed(
            title=f"📊 詳細分析: {keyword}",
            color=discord.Color.green()
        )
        embed.add_field(name="現在順位", value=f"**{serp_result.rank}位**", inline=True)
        
        if own_metrics and competitor_metrics:
            char_diff = own_metrics.char_count - competitor_metrics.char_count
            heading_diff = len(own_metrics.headings) - len(competitor_metrics.headings)
            img_diff = own_metrics.image_count - competitor_metrics.image_count
            
            metrics_text = f"""```
{'項目':<10} {'自社':>8} {'競合':>8} {'差分':>8}
{'-'*38}
{'文字数':<10} {own_metrics.char_count:>8,} {competitor_metrics.char_count:>8,} {char_diff:>+8,}
{'見出し':<10} {len(own_metrics.headings):>8} {len(competitor_metrics.headings):>8} {heading_diff:>+8}
{'画像':<10} {own_metrics.image_count:>8} {competitor_metrics.image_count:>8} {img_diff:>+8}
```"""
            embed.add_field(name="📈 コンテンツ比較", value=metrics_text, inline=False)
            
            if competitor:
                embed.add_field(
                    name="🏆 比較対象",
                    value=f"{competitor.get('title', 'N/A')[:60]}\n{competitor.get('url', '')}",
                    inline=False
                )
            
            # Get AI analysis (run in thread pool)
            prompt = build_gemini_prompt(keyword, serp_result, config.own_domain, own_metrics, competitor_metrics)
            gaps = await asyncio.to_thread(request_gemini, config, prompt)
            
            if gaps:
                analysis_text = "\n".join([f"• {gap}" for gap in gaps[:5]])
                embed.add_field(name="🤖 AI分析", value=analysis_text, inline=False)
        
        # Store context for conversation
        user_context[interaction.user.id] = AnalysisContext(
            keyword=keyword,
            rank=serp_result.rank,
            own_url=serp_result.own_url,
            own_metrics=own_metrics,
            competitor_url=competitor.get("url") if competitor else None,
            competitor_metrics=competitor_metrics,
            gaps=gaps
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logging.error(f"Error in analyze command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.tree.command(name="status", description="Bot のステータスと利用可能なキーワードを表示")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        # Load keywords from sheet (run in thread pool)
        entries = await asyncio.to_thread(load_keywords, config)
        
        embed = discord.Embed(
            title="📊 SEO Bot ステータス",
            color=discord.Color.purple()
        )
        embed.add_field(name="🤖 Bot", value="オンライン", inline=True)
        embed.add_field(name="📝 登録キーワード数", value=f"{len(entries)}件", inline=True)
        
        if entries:
            keywords_list = "\n".join([f"• {entry.keyword}" for entry in entries[:10]])
            if len(entries) > 10:
                keywords_list += f"\n... 他 {len(entries) - 10}件"
            embed.add_field(name="キーワード一覧", value=keywords_list, inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logging.error(f"Error in status command: {e}", exc_info=True)
        await interaction.followup.send(f"❌ エラーが発生しました: {str(e)}")

@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Check if bot is mentioned
    if bot.user in message.mentions:
        # Remove mention from content
        content = message.content.replace(f'<@{bot.user.id}>', '').strip()
        
        if not content:
            await message.channel.send("何かお手伝いできることはありますか？ `/rank`, `/analyze`, `/status` コマンドを使ってみてください！")
            return
        
        # Check if user has recent analysis context
        ctx = user_context.get(message.author.id)
        
        # Use Gemini for chat response with context
        try:
            if ctx:
                # Build context-aware prompt
                context_info = f"""
直近の分析情報:
- キーワード: {ctx.keyword}
- 自社順位: {ctx.rank}位
- 自社URL: {ctx.own_url}
- 自社文字数: {ctx.own_metrics.char_count if ctx.own_metrics else 'N/A'}文字
- 自社見出し数: {len(ctx.own_metrics.headings) if ctx.own_metrics else 'N/A'}個
- 自社画像数: {ctx.own_metrics.image_count if ctx.own_metrics else 'N/A'}枚
- 競合文字数: {ctx.competitor_metrics.char_count if ctx.competitor_metrics else 'N/A'}文字
- 競合見出し数: {len(ctx.competitor_metrics.headings) if ctx.competitor_metrics else 'N/A'}個
- AI分析結果: {', '.join(ctx.gaps[:3]) if ctx.gaps else 'N/A'}
"""
                prompt = f"""あなたはSEOの専門家です。以下の分析データを参照して、ユーザーの質問に日本語で答えてください。

{context_info}

ユーザーの質問: {content}

回答は300文字以内で、上記のデータを具体的に引用しながら実用的なアドバイスを含めてください。
「この記事」「自社記事」などと言われたら、上記の自社URLの記事を指します。"""
            else:
                # No context, general SEO question
                prompt = f"""あなたはSEOの専門家です。以下の質問に日本語で簡潔に答えてください。

質問: {content}

回答は200文字以内で、実用的なアドバイスを含めてください。
より詳しい分析が必要な場合は `/analyze [キーワード]` コマンドの使用を提案してください。"""
            
            response = await asyncio.to_thread(request_gemini, config, prompt)
            if response:
                answer = "\n".join(response)
                if ctx:
                    await message.channel.send(f"💡 **[{ctx.keyword}]に関する回答:**\n{answer}")
                else:
                    await message.channel.send(f"💡 {answer}")
            else:
                await message.channel.send("申し訳ございません。回答を生成できませんでした。")
        except Exception as e:
            logging.error(f"Error in chat: {e}", exc_info=True)
            await message.channel.send("エラーが発生しました。")
    
    await bot.process_commands(message)

def main():
    setup_logging(config)
    
    if not config.discord_bot_token:
        logging.error("DISCORD_BOT_TOKEN is not set in environment variables")
        return
    
    logging.info("Starting Discord bot...")
    bot.run(config.discord_bot_token)

if __name__ == "__main__":
    main()
