import os
import json
import threading
import asyncio
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TUNISIA_TZ = pytz.timezone('Africa/Tunis')

# ==========================================
# 0. PERSISTENT STORAGE FUNCTIONS (JSON)
# ==========================================
DATA_FILE = "settings.json"

def load_data():
    """Loads saved settings from the JSON file if it exists."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading settings file: {e}")
    return {"active_channels": {}, "merchant_roles": {}}

def save_data(data):
    """Saves current settings to the JSON file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"⚠️ Error saving settings file: {e}")

# Load stored data into memory at startup
db = load_data()
active_channels = db.get("active_channels", {})  # Format: {guild_id_str: channel_id}
merchant_roles = db.get("merchant_roles", {})    # Format: {guild_id_str: {merchant_name: role_id}}


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

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
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
@tasks.loop(minutes=1)
async def merchant_announcement_loop():
    now = datetime.now(TUNISIA_TZ)
    if now.minute != 0:
        return
    current_hour = now.hour
    unix_timestamp = int((datetime.now() + timedelta(minutes=15)).timestamp())

    for guild_id_str, channel_id in active_channels.items():
        guild = bot.get_guild(int(guild_id_str))
        if not guild:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        guild_roles = merchant_roles.get(guild_id_str, {})

        if current_hour % 3 == 0:
            merchant_type = "Ancient Merchant"
            role_id = guild_roles.get(merchant_type)
            role_mention = f"<@&{role_id}>" if role_id else "@here"
            
            embed = discord.Embed(title="🛒 Merchant Alert!", description="🎟️ **Ancient Ticket Merchant** has arrived!", color=discord.Color.gold())
            embed.add_field(name="Status", value=f"Leaves <t:{unix_timestamp}:R>", inline=False)
            
            try:
                await channel.send(content=role_mention, embed=embed)
            except Exception as e:
                print(f"Failed to send merchant notification in guild {guild_id_str}: {e}")

        elif (current_hour - 1) % 3 == 0:
            merchant_type = "Honey & Dungeon Merchant"
            role_id = guild_roles.get(merchant_type)
            role_mention = f"<@&{role_id}>" if role_id else "@here"
            
            embed = discord.Embed(title="🛒 Merchant Alert!", description="🍯 **Honey & Dungeon Merchant** has arrived!", color=discord.Color.orange())
            embed.add_field(name="Status", value=f"Leaves <t:{unix_timestamp}:R>", inline=False)
            
            try:
                await channel.send(content=role_mention, embed=embed)
            except Exception as e:
                print(f"Failed to send merchant notification in guild {guild_id_str}: {e}")

        elif (current_hour - 2) % 3 == 0:
            merchant_type = "Paradox Merchant"
            role_id = guild_roles.get(merchant_type)
            role_mention = f"<@&{role_id}>" if role_id else "@here"
            
            embed = discord.Embed(title="🛒 Merchant Alert!", description="⏰ **Paradox Merchant** has arrived!", color=discord.Color.blue())
            embed.add_field(name="Status", value=f"Leaves <t:{unix_timestamp}:R>", inline=False)
            
            try:
                await channel.send(content=role_mention, embed=embed)
            except Exception as e:
                print(f"Failed to send merchant notification in guild {guild_id_str}: {e}")

@merchant_announcement_loop.before_loop
async def before_merchant_loop():
    await bot.wait_until_ready()


# ==========================================
# 4. CUSTOM SLASH COMMANDS
# ==========================================

@bot.tree.command(name="activate_merchants", description="Enable merchant notifications in a specific channel.")
@app_commands.describe(channel="The channel where merchant alerts will be sent")
@app_commands.default_permissions(manage_channels=True)
async def activate_merchants(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    active_channels[guild_id] = channel.id
    
    # Save updates to disk
    save_data({"active_channels": active_channels, "merchant_roles": merchant_roles})
    
    await interaction.response.send_message(f"✅ Merchant notifications have been activated and set to {channel.mention}!", ephemeral=True)


@bot.tree.command(name="desactivate_merchants", description="Stop sending merchant notifications.")
@app_commands.default_permissions(manage_channels=True)
async def desactivate_merchants(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    if guild_id in active_channels:
        del active_channels[guild_id]
        
        # Save updates to disk
        save_data({"active_channels": active_channels, "merchant_roles": merchant_roles})
        
        await interaction.response.send_message("🛑 Merchant notifications have been deactivated.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Merchant notifications are not currently active in this server.", ephemeral=True)


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
    guild_id = str(interaction.guild.id)
    if guild_id not in merchant_roles:
        merchant_roles[guild_id] = {}
    
    merchant_roles[guild_id][merchant_name] = role.id
    
    # Save updates to disk
    save_data({"active_channels": active_channels, "merchant_roles": merchant_roles})
    
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