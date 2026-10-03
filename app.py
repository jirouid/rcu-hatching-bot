import os
import threading
import aiohttp
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- 1. Flask Web Server (Keeps Render Free Tier Alive) ---
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Discord Bot is active and running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- 2. Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# --- 3. Example Starter Command (With Best Practices) ---
@bot.tree.command(name="ping", description="Test if the bot is responding.")
async def ping(interaction: discord.Interaction):
    # Pro-tip: Always defer heavy commands to avoid the 3-second timeout!
    await interaction.response.defer(thinking=True, ephemeral=True)
    
    # Example using aiohttp for safe asynchronous web requests
    async with aiohttp.ClientSession() as session:
        async with session.get("https://httpbin.org/ip") as resp:
            data = await resp.json() if resp.status == 200 else {}
            origin_ip = data.get("origin", "Unknown")

    await interaction.followup.send(f"🏓 Pong! Bot is online and healthy.\nServer IP hint: `{origin_ip}`")

# --- 4. Run Both Flask & Discord Simultaneously ---
if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Start Discord Bot on the main thread
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN is missing!")