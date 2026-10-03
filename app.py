import os
import threading
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import aiohttp
from pymongo import MongoClient
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
TUNISIA_TZ = pytz.timezone('Africa/Tunis')

# ==========================================
# 0. MONGODB DATABASE SETUP
# ==========================================
if not MONGO_URI:
    print("❌ Error: MONGO_URI environment variable is missing!")

mongo_client = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True)
db = mongo_client["discord_bot_db"]

# Collections
settings_col = db["settings"]

def load_setting(key, default):
    doc = settings_col.find_one({"_id": key})
    return doc["value"] if doc else default

def save_setting(key, value):
    settings_col.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)

# Load stored data into memory at startup
active_channels = load_setting("active_channels", {})      # Format: {guild_id_str: channel_id}
hatching_channels = load_setting("hatching_channels", {})  # Format: {guild_id_str: channel_id}
merchant_roles = load_setting("merchant_roles", {})        # Format: {guild_id_str: {merchant_name: role_id}}
linked_accounts = load_setting("linked_accounts", {})      # Format: {discord_user_id_str: [{"username": str, "id": int}]}
processed_hatches = load_setting("processed_hatches", [])  # List of recently announced hatch IDs (max 200 kept)


# ==========================================
# 1. FLASK WEB SERVER (Uptime Keep-Alive)
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Discord Bot is active and running with MongoDB!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# 2. DISCORD BOT SETUP
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    if not merchant_announcement_loop.is_running():
        merchant_announcement_loop.start()
    if not hatching_announcement_loop.is_running():
        hatching_announcement_loop.start()

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# ==========================================
# 3. BACKGROUND TASKS (Merchants & Hatches)
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
            
            embed = discord.Embed(title="🛒 Merchant Alert!", description="🎟 **Ancient Ticket Merchant** has arrived!", color=discord.Color.gold())
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


def format_asset_url(asset_str):
    if asset_str and asset_str.startswith("rbxassetid://"):
        asset_id = asset_str.replace("rbxassetid://", "")
        return f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}"
    return None

def country_to_flag(country_code):
    if not country_code or len(country_code) != 2:
        return ""
    code = country_code.upper()
    return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))

async def fetch_roblox_avatar(session, user_id):
    if not user_id:
        return None
    try:
        url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=false"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                data_list = data.get("data", [])
                if data_list:
                    return data_list[0].get("imageUrl")
    except Exception as e:
        print(f"Error fetching Roblox avatar for user {user_id}: {e}")
    return None

async def build_hatch_embed_from_data(session, hatch, display_name):
    pets_directory = {}
    try:
        async with session.get("https://public-api.powerfulstudio.xyz/rcu/v1/directories/pets") as resp:
            if resp.status == 200:
                res_json = await resp.json()
                pets_directory = res_json.get("entries", res_json)
    except Exception as e:
        print(f"Error fetching pet directory: {e}")

    clan_tag = hatch.get("clanTag", "")
    clan_display = f"[{clan_tag}] " if clan_tag else ""
    country_code = hatch.get("countryCode", "")
    flag = country_to_flag(country_code)
    
    egg_name = hatch.get("eggName", "Unknown")
    eggs_opened = hatch.get("eggsOpened", 0)
    
    chance = hatch.get("chance", 0)
    player_chance = hatch.get("playerChance", 0)
    serial = hatch.get("serial", 0)
    user_id = hatch.get("userId")
    
    item_info = hatch.get("item", {})
    item_name = item_info.get("name", "Unknown Pet")
    tier = item_info.get("tier", 1) 
    is_shiny = item_info.get("shiny", False)

    pet_data = {}
    if isinstance(pets_directory, dict):
        pet_data = pets_directory.get(item_name, {})
        if not pet_data:
            for k, v in pets_directory.items():
                if k.lower() == item_name.lower():
                    pet_data = v
                    break
    elif isinstance(pets_directory, list):
        for entry in pets_directory:
            if isinstance(entry, dict) and entry.get("name", "").lower() == item_name.lower():
                pet_data = entry
                break

    rarity = pet_data.get("rarity", "secret").lower() if isinstance(pet_data, dict) else "secret"
    images = pet_data.get("images", []) if isinstance(pet_data, dict) else []

    image_url = None
    img_index = tier - 1 if 0 <= (tier - 1) < len(images) else 0
    if images:
        image_url = format_asset_url(images[img_index])

    avatar_url = await fetch_roblox_avatar(session, user_id)

    prefix_parts = []
    if tier == 2:
        prefix_parts.append("Golden")
    elif tier == 3:
        prefix_parts.append("Toxic")
    elif tier == 4:
        prefix_parts.append("Galaxy")
    
    if rarity and rarity != "secret":
        prefix_parts.append(rarity.capitalize())
    else:
        prefix_parts.append("Secret")

    tier_prefix = " ".join(prefix_parts)
    if is_shiny:
        tier_prefix = f"Shiny {tier_prefix} ✨"

    embed_color = discord.Color.red()
    if tier == 2:
        embed_color = discord.Color.from_str("#ffd024")
    elif tier == 3:
        embed_color = discord.Color.from_str("#57ed4c")
    elif tier == 4:
        embed_color = discord.Color.from_str("#b811ff")
    else:
        if rarity == "secret":
            embed_color = discord.Color.from_str("#fd4649")
        elif rarity == "divine":
            embed_color = discord.Color.from_str("#ffee00")
        elif rarity == "supreme":
            embed_color = discord.Color.from_str("#ff6600")
        elif rarity == "mysterious":
            embed_color = discord.Color.from_str("#9400fd")
        elif rarity == "ultimate":
            embed_color = discord.Color.from_str("#08ff00")

    formatted_eggs_opened = f"{eggs_opened:,.0f}" if eggs_opened < 1000000 else f"{eggs_opened / 1000000:.2f}M" if eggs_opened < 1000000000 else f"{eggs_opened / 1000000000:.2f}B"
    
    formatted_chance = f"1/{int(1/chance):,}" if chance > 0 else "N/A"
    formatted_player_chance = f"1/{int(1/player_chance):,}" if player_chance > 0 else "N/A"

    embed = discord.Embed(color=embed_color, timestamp=datetime.now())
    embed.set_author(name=f"{clan_display}{display_name}", icon_url=avatar_url if avatar_url else discord.Embed.Empty)
    
    if image_url:
        embed.set_thumbnail(url=image_url)

    # Invisible spacer formatting blocks to enforce uniform width (~1.5x visual width)
    spacer = "᠎" * 45

    embed.add_field(
        name="", 
        value=f"### 🔥 Congrats! {flag} {display_name} hatched a\n### {tier_prefix} {item_name}!\n{spacer}", 
        inline=False
    )
    
    embed.add_field(
        name="", 
        value=(
            f"🥚 **Egg:** {egg_name} (`{formatted_eggs_opened} opened`)\n\n"
            f"🎲 **Rarity:** `{formatted_chance}`\n\n"
            f"⭐ **Serial:** `#{serial}`\n"
            f"{spacer}"
        ), 
        inline=False
    )
    
    embed.add_field(
        name="📘 **Player's Stats:**", 
        value=(
            f"Total Eggs Opened: {formatted_eggs_opened}\n\n"
            f"Rarity: `{formatted_player_chance}`"
        ), 
        inline=False
    )

    return embed


@tasks.loop(seconds=3)
async def hatching_announcement_loop():
    if not hatching_channels:
        return

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://public-api.powerfulstudio.xyz/rcu/v1/pet-hatches") as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                hatches = data.get("petHatches", [])
        except Exception as e:
            print(f"Error fetching pet hatches: {e}")
            return

        global processed_hatches
        new_hatches_found = False

        for hatch in reversed(hatches):
            hatch_id = hatch.get("id")
            if hatch_id is None or hatch_id in processed_hatches:
                continue

            processed_hatches.append(hatch_id)
            new_hatches_found = True

            if hatch.get("anonymous", False):
                continue

            user_id = hatch.get("userId")
            
            matched_discord_id = None
            roblox_username = None
            for d_id, accounts in linked_accounts.items():
                for acc in accounts:
                    if acc["id"] == user_id:
                        matched_discord_id = d_id
                        roblox_username = acc["username"]
                        break
                if matched_discord_id:
                    break

            if not matched_discord_id:
                continue

            embed = await build_hatch_embed_from_data(session, hatch, roblox_username)

            for guild_id_str, chan_id in hatching_channels.items():
                guild = bot.get_guild(int(guild_id_str))
                if not guild:
                    continue
                channel = guild.get_channel(chan_id)
                if not channel:
                    continue
                
                try:
                    message_content = f"> Congrats <@{matched_discord_id}> ! 🎉"
                    await channel.send(content=message_content, embed=embed)
                except Exception as e:
                    print(f"Failed to send hatch notification in guild {guild_id_str}: {e}")

        if new_hatches_found:
            if len(processed_hatches) > 200:
                processed_hatches = processed_hatches[-200:]
            save_setting("processed_hatches", processed_hatches)

@hatching_announcement_loop.before_loop
async def before_hatching_loop():
    await bot.wait_until_ready()


# ==========================================
# 4. CUSTOM SLASH COMMANDS (Config & Testing)
# ==========================================

@bot.tree.command(name="activate_merchants", description="Enable merchant notifications in a specific channel.")
@app_commands.describe(channel="The channel where merchant alerts will be sent")
@app_commands.default_permissions(manage_channels=True)
async def activate_merchants(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    active_channels[guild_id] = channel.id
    save_setting("active_channels", active_channels)
    await interaction.response.send_message(f"✅ Merchant notifications have been activated and set to {channel.mention}!", ephemeral=True)


@bot.tree.command(name="activate_hatching", description="Set the channel for hatching alerts.")
@app_commands.describe(channel="The channel where hatching notifications will be sent")
@app_commands.default_permissions(manage_channels=True)
async def activate_hatching(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    hatching_channels[guild_id] = channel.id
    save_setting("hatching_channels", hatching_channels)
    await interaction.response.send_message(f"✅ Hatching channel has been set to {channel.mention}!", ephemeral=True)


@bot.tree.command(name="deactivate_merchants", description="Stop sending merchant notifications.")
@app_commands.default_permissions(manage_channels=True)
async def deactivate_merchants(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    if guild_id in active_channels:
        del active_channels[guild_id]
        save_setting("active_channels", active_channels)
        await interaction.response.send_message("🛑 Merchant notifications have been deactivated.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Merchant notifications are not currently active in this server.", ephemeral=True)


@bot.tree.command(name="deactivate_hatching", description="Stop sending hatching notifications.")
@app_commands.default_permissions(manage_channels=True)
async def deactivate_hatching(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    if guild_id in hatching_channels:
        del hatching_channels[guild_id]
        save_setting("hatching_channels", hatching_channels)
        await interaction.response.send_message("🛑 Hatching notifications have been deactivated.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Hatching notifications are not currently active in this server.", ephemeral=True)


@bot.tree.command(name="link_role_to_merchant", description="Link a specific role to ping for a chosen merchant.")
@app_commands.describe(merchant_name="Select the merchant type", role="The role to ping when this merchant appears")
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
    save_setting("merchant_roles", merchant_roles)
    await interaction.response.send_message(f"🔗 Successfully linked **{merchant_name}** notifications to role {role.mention}!", ephemeral=True)


@bot.tree.command(name="test_merchant", description="Force-spawn a merchant test alert immediately in this server.")
@app_commands.describe(merchant_name="Choose which merchant to test")
@app_commands.choices(merchant_name=[
    app_commands.Choice(name="Honey & Dungeon Merchant", value="Honey & Dungeon Merchant"),
    app_commands.Choice(name="Ancient Merchant", value="Ancient Merchant"),
    app_commands.Choice(name="Paradox Merchant", value="Paradox Merchant")
])
@app_commands.default_permissions(manage_channels=True)
async def test_merchant(interaction: discord.Interaction, merchant_name: str):
    guild_id_str = str(interaction.guild.id)
    channel_id = active_channels.get(guild_id_str)
    
    if not channel_id:
        await interaction.response.send_message("⚠️ No merchant channel is activated here! Use `/activate_merchants` first.", ephemeral=True)
        return
        
    channel = interaction.guild.get_channel(channel_id)
    if not channel:
        await interaction.response.send_message("⚠️ The configured merchant channel could not be found.", ephemeral=True)
        return

    guild_roles = merchant_roles.get(guild_id_str, {})
    role_id = guild_roles.get(merchant_name)
    role_mention = f"<@&{role_id}>" if role_id else "@here"
    
    unix_timestamp = int((datetime.now() + timedelta(minutes=15)).timestamp())

    if merchant_name == "Ancient Merchant":
        embed = discord.Embed(title="🛒 Merchant Alert [TEST]", description="🎟️ **Ancient Ticket Merchant** has arrived!", color=discord.Color.gold())
    elif merchant_name == "Honey & Dungeon Merchant":
        embed = discord.Embed(title="🛒 Merchant Alert [TEST]", description="🍯 **Honey & Dungeon Merchant** has arrived!", color=discord.Color.orange())
    else:
        embed = discord.Embed(title="🛒 Merchant Alert [TEST]", description="⏰ **Paradox Merchant** has arrived!", color=discord.Color.blue())

    embed.add_field(name="Status", value=f"Leaves <t:{unix_timestamp}:R>", inline=False)
    
    await channel.send(content=role_mention, embed=embed)
    await interaction.response.send_message(f"✅ Test alert for **{merchant_name}** successfully sent to {channel.mention}!", ephemeral=True)


@bot.tree.command(name="show_global_hatch", description="Test command: Pulls and displays the absolute latest global hatch right now.")
@app_commands.default_permissions(manage_channels=True)
async def show_global_hatch(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://public-api.powerfulstudio.xyz/rcu/v1/pet-hatches") as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Failed to reach API (Status code: {resp.status})", ephemeral=True)
                    return
                data = await resp.json()
                hatches = data.get("petHatches", [])
                
                if not hatches:
                    await interaction.followup.send("⚠️ API returned zero pet hatches.", ephemeral=True)
                    return
                
                latest_hatch = hatches[0]
                fake_username = "TestUser"
                embed = await build_hatch_embed_from_data(session, latest_hatch, fake_username)
                
                await interaction.followup.send(content=f"> Congrats <@{interaction.user.id}> ! 🎉", embed=embed, ephemeral=False)
        except Exception as e:
            await interaction.followup.send(f"❌ Error fetching from API: {e}", ephemeral=True)


@bot.tree.command(name="bot_info", description="Displays bot configurations and linked accounts for this server.")
async def bot_info(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    guild_id_str = str(interaction.guild.id)

    m_chan_id = active_channels.get(guild_id_str)
    h_chan_id = hatching_channels.get(guild_id_str)
    
    m_chan_str = f"<#{m_chan_id}>" if m_chan_id else "Not Set"
    h_chan_str = f"<#{h_chan_id}>" if h_chan_id else "Not Set"

    guild_roles = merchant_roles.get(guild_id_str, {})
    merchants = ["Ancient Merchant", "Honey & Dungeon Merchant", "Paradox Merchant"]
    roles_text = ""
    for m in merchants:
        r_id = guild_roles.get(m)
        r_mention = f"<@&{r_id}>" if r_id else "Not Linked"
        roles_text += f"- **{m}**: {r_mention}\n"

    linked_users_text = ""
    for discord_uid, accounts in linked_accounts.items():
        member = interaction.guild.get_member(int(discord_uid))
        if member:
            count = len(accounts)
            account_word = "accounts linked" if count > 1 else "account linked"
            linked_users_text += f"- {member.name} ({count} {account_word})\n"

    if not linked_users_text:
        linked_users_text = "No server members have linked accounts yet."

    embed = discord.Embed(title=f"📊 Bot Status & Info: {interaction.guild.name}", color=discord.Color.dark_blue())
    embed.add_field(name="📢 Configured Channels", value=f"**merchants_channel:** {m_chan_str}\n**hatching_channel:** {h_chan_str}", inline=False)
    embed.add_field(name="🛡️ Linked Merchant Roles", value=roles_text, inline=False)
    embed.add_field(name="🔗 Server Members with Linked Accounts", value=linked_users_text, inline=False)
    embed.set_footer(text="Requested via /bot_info")

    await interaction.followup.send(embed=embed)


# ==========================================
# 5. ROBLOX ACCOUNT LINKING COMMANDS
# ==========================================

@bot.tree.command(name="connect", description="Connect a Roblox account using username or ID (supports multiple accounts)")
@app_commands.describe(username="Your Roblox username or Roblox ID")
async def connect(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    
    roblox_id = None
    roblox_name = None

    async with aiohttp.ClientSession() as session:
        if username.isdigit():
            url = f"https://users.roblox.com/v1/users/{username}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    roblox_id = data.get("id")
                    roblox_name = data.get("name")
        else:
            payload = {"usernames": [username], "excludeBannedUsers": True}
            async with session.post("https://users.roblox.com/v1/usernames/users", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    users = data.get("data", [])
                    if users:
                        roblox_id = users[0]["id"]
                        roblox_name = users[0]["name"]

    if not roblox_id or not roblox_name:
        await interaction.followup.send(f"⚠️️ Could not verify a valid Roblox account with input: **{username}**.", ephemeral=True)
        return

    discord_user_id = str(interaction.user.id)
    if discord_user_id not in linked_accounts:
        linked_accounts[discord_user_id] = []

    if any(acc["id"] == roblox_id for acc in linked_accounts[discord_user_id]):
        await interaction.followup.send(f"⚠️ The Roblox account **{roblox_name}** (`ID: {roblox_id}`) is already connected to your profile!", ephemeral=True)
        return

    linked_accounts[discord_user_id].append({"username": roblox_name, "id": roblox_id})
    save_setting("linked_accounts", linked_accounts)

    await interaction.followup.send(f"✅ Successfully connected Roblox account **{roblox_name}** (`ID: {roblox_id}`) to your profile!", ephemeral=True)


@bot.tree.command(name="disconnect", description="Disconnect a Roblox account from your profile using its username or ID")
@app_commands.describe(username="The Roblox username or ID you want to remove")
async def disconnect(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    
    discord_user_id = str(interaction.user.id)
    user_accounts = linked_accounts.get(discord_user_id, [])

    if not user_accounts:
        await interaction.followup.send("⚠️ You don't have any Roblox accounts linked to your profile.", ephemeral=True)
        return

    found_account = None
    for acc in user_accounts:
        if str(acc["id"]) == username or acc["username"].lower() == username.lower():
            found_account = acc
            break

    if not found_account:
        await interaction.followup.send(f"⚠️ Could not find a linked account matching **{username}** in your profile.", ephemeral=True)
        return

    user_accounts.remove(found_account)
    if not user_accounts:
        del linked_accounts[discord_user_id]

    save_setting("linked_accounts", linked_accounts)

    await interaction.followup.send(f"✅ Successfully disconnected **{found_account['username']}** (`ID: {found_account['id']}`) from your profile.", ephemeral=True)


@bot.tree.command(name="account_info", description="View connected Roblox accounts for yourself or another user")
@app_commands.describe(user="Optional: Choose a Discord user to check")
async def account_info(interaction: discord.Interaction, user: discord.User = None):
    await interaction.response.defer(ephemeral=False)
    
    target_user = user if user else interaction.user
    discord_user_id = str(target_user.id)
    user_accounts = linked_accounts.get(discord_user_id, [])

    if not user_accounts:
        msg = f"⚠️ **{target_user.name}** doesn't have any Roblox accounts linked yet!" if user else "⚠️ You don't have any Roblox accounts linked yet! Use `/connect` first."
        await interaction.followup.send(msg, ephemeral=True)
        return

    account_lines = [f"{i}. {acc['username']} (ID: {acc['id']})" for i, acc in enumerate(user_accounts, 1)]
    formatted_text = f"Your Connected Accounts ({len(user_accounts)})\n" + "\n".join(account_lines)

    embed = discord.Embed(
        title=f"🔗 Connected Accounts for {target_user.name}",
        description=f"```text\n{formatted_text}\n```",
        color=discord.Color.blue()
    )
    await interaction.followup.send(embed=embed)


# ==========================================
# 6. START BOTH THREADS
# ==========================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN is missing!")