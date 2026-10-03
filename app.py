import os
import threading
import asyncio
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ==========================================
# 1. FLASK WEB SERVER (Uptime Keep-Alive)
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Discord Bot is active and running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# 2. DISCORD BOT SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database dictionaries stored in memory while running
active_channels = {}  # Format: {guild_id: channel_id}
merchant_roles = {}   # Format: {guild_id: {merchant_name: role_id}}


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    # Start background loops if not already running
    if not merchant_announcement_loop.is_running():
        merchant_announcement_loop.start()

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# ==========================================
# 3. MERCHANT BACKGROUND TASK & TIMING
# ==========================================
@tasks.loop(minutes=30) # Adjust interval checks as needed for your timers
async def merchant_announcement_loop():
    # Loop through configured guilds and send merchant alerts
    for guild_id, channel_id in active_channels.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        # Get role mappings for this specific guild
        guild_roles = merchant_roles.get(guild_id, {})

        # Example trigger for Honey & Dungeon Merchant
        # (You can separate or customize intervals per merchant type as desired)
        honey_role_id = guild_roles.get("Honey & Dungeon Merchant")
        honey_mention = f"<@&{honey_role_id}>" if honey_role_id else "@here"

        embed = discord.Embed(
            title="🍯 Honey & Dungeon Merchant Has Appeared!",
            description="The merchant has spawned in-game! Grab your items before they leave.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Available Merchant", value="Honey & Dungeon Merchant", inline=False)
        embed.set_footer(text="Check the game now!")

        try:
            await channel.send(content=honey_mention, embed=embed)
        except Exception as e:
            print(f"Failed to send merchant notification in guild {guild_id}: {e}")

@merchant_announcement_loop.before_loop
async def before_merchant_loop():
    await bot.wait_until_ready()


# ==========================================
# 4. CUSTOM SLASH COMMANDS
# ==========================================

# 1. Activate Merchants in a specific channel
@bot.tree.command(name="activate_merchants", description="Enable merchant notifications in a specific channel.")
@app_commands.describe(channel="The channel where merchant alerts will be sent")
@app_commands.default_permissions(manage_channels=True)
async def activate_merchants(interaction: discord.Interaction, channel: discord.TextChannel):
    active_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(f"✅ Merchant notifications have been activated and set to {channel.mention}!", ephemeral=True)


# 2. Deactivate Merchants
@bot.tree.command(name="desactivate_merchants", description="Stop sending merchant notifications.")
@app_commands.default_permissions(manage_channels=True)
async def desactivate_merchants(interaction: discord.Interaction):
    if interaction.guild.id in active_channels:
        del active_channels[interaction.guild.id]
        await interaction.response.send_message("🛑 Merchant notifications have been deactivated.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Merchant notifications are not currently active in this server.", ephemeral=True)


# 3. Link Role to Merchant with Choices
@bot.tree.command(name="link_role_to_merchant", description="Link a specific role to ping for a chosen merchant.")
@app_commands.describe(
    merchant_name="Select the merchant type",
    role="The role to ping when this merchant appears"
)
@app_commands.choices(merchant_name=[
    app_commands.Choice(name="Honey & Dungeon Merchant", value="Honey & Dungeon Merchant"),
    app_commands.Choice(name="Ancient Merchant", value="Ancient Merchant"),
    app_commands.Choice(name="Paradox Merchant", value="Paradox Merchant")
])
@app_commands.default_permissions(manage_roles=True)
async def link_role_to_merchant(interaction: discord.Interaction, merchant_name: str, role: discord.Role):
    if interaction.guild.id not in merchant_roles:
        merchant_roles[interaction.guild.id] = {}
    
    merchant_roles[interaction.guild.id][merchant_name] = role.id
    await interaction.response.send_message(f"🔗 Successfully linked **{merchant_name}** notifications to role {role.mention}!", ephemeral=True)


# ==========================================
# 5. START BOTH THREADS
# ==========================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN is missing!")