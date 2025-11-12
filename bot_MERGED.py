import os
import json
import discord
import asyncio
import time
import io
from discord.ext import commands, tasks
from discord import ui
from discord import app_commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Bot setup with command prefix '!'
intents = discord.Intents.all()  # Enable all intents
bot = commands.Bot(command_prefix='!', intents=intents, reconnect=True)

# Points, rewards, and vouch roles data structure - all guild-specific
points_data = {}
rewards_data = {}
vouch_roles_data = {}
verification_channels = {}  # guild_id: channel_id
pending_vouches = {}  # vouch_id: {guild_id, user_id, message_id, channel_id, mentioned_role, image_url}
# Cooldown tracking - user_id: timestamp
user_last_vouch_time = {}
COOLDOWN_MINUTES = 5

# Guild-specific helper functions for points
def get_guild_points(guild_id):
    """Get points data for a specific guild"""
    guild_id = str(guild_id)
    if guild_id not in points_data:
        points_data[guild_id] = {}
    return points_data[guild_id]

def get_guild_rewards(guild_id):
    """Get rewards data for a specific guild"""
    guild_id = str(guild_id)
    if guild_id not in rewards_data:
        rewards_data[guild_id] = {}
    return rewards_data[guild_id]

def get_guild_vouch_roles(guild_id):
    """Get vouch roles for a specific guild"""
    guild_id = str(guild_id)
    if guild_id not in vouch_roles_data:
        vouch_roles_data[guild_id] = ["CHEF"]  # Default to "CHEF" role for NEW guilds only
        save_vouch_roles()  # Save immediately to ensure persistence
    return vouch_roles_data[guild_id].copy()  # Return a copy to prevent reference issues

def get_user_points(guild_id, user_id):
    """Get points for a specific user in a specific guild"""
    guild_points = get_guild_points(guild_id)
    return guild_points.get(str(user_id), 0)

def set_user_points(guild_id, user_id, points):
    """Set points for a specific user in a specific guild"""
    guild_points = get_guild_points(guild_id)
    guild_points[str(user_id)] = points
    save_points()

def add_user_points(guild_id, user_id, points_to_add):
    """Add points to a specific user in a specific guild"""
    current_points = get_user_points(guild_id, user_id)
    set_user_points(guild_id, user_id, current_points + points_to_add)

# File operations
def load_points():
    try:
        with open('points.json', 'r') as f:
            data = json.load(f)
            # Convert old format to guild-specific format if needed
            if data and not any(str(k).isdigit() and len(str(k)) > 15 for k in data.keys()):
                # Old format detected, return as-is for backward compatibility
                return data
            return data
    except FileNotFoundError:
        return {}

def save_points():
    with open('points.json', 'w') as f:
        json.dump(points_data, f, indent=4)

def load_rewards():
    try:
        with open('rewards.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_rewards():
    with open('rewards.json', 'w') as f:
        json.dump(rewards_data, f, indent=4)

def load_vouch_roles():
    try:
        with open('vouch_roles.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_vouch_roles():
    with open('vouch_roles.json', 'w') as f:
        json.dump(vouch_roles_data, f, indent=4)

def reset_guild_vouch_roles(guild_id):
    """Reset vouch roles for a specific guild to default"""
    guild_id = str(guild_id)
    vouch_roles_data[guild_id] = ["CHEF"]
    save_vouch_roles()

def load_verification_channels():
    """Load verification channel settings"""
    try:
        with open('verification_channels.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_verification_channels():
    """Save verification channel settings"""
    with open('verification_channels.json', 'w') as f:
        json.dump(verification_channels, f, indent=4)

def get_verification_channel(guild_id):
    """Get verification channel ID for a guild"""
    guild_id = str(guild_id)
    return verification_channels.get(guild_id)

def set_verification_channel(guild_id, channel_id):
    """Set verification channel for a guild"""
    guild_id = str(guild_id)
    verification_channels[guild_id] = str(channel_id)
    save_verification_channels()

# Button view for vouch approval
class VouchApprovalView(ui.View):
    def __init__(self, vouch_id):
        super().__init__(timeout=None)  # No timeout - buttons stay active
        self.vouch_id = vouch_id
    
    @ui.button(label="✅ Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_approval(interaction, approved=True)
    
    @ui.button(label="❌ Deny", style=discord.ButtonStyle.red, emoji="❌")
    async def deny_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_approval(interaction, approved=False)
    
    async def handle_approval(self, interaction: discord.Interaction, approved: bool):
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You need administrator permissions to approve/deny vouches!",
                ephemeral=True
            )
            return
        
        if self.vouch_id not in pending_vouches:
            await interaction.response.send_message(
                "❌ This vouch has already been processed or doesn't exist!",
                ephemeral=True
            )
            return
        
        vouch_data = pending_vouches[self.vouch_id]
        guild_id = vouch_data['guild_id']
        user_id = vouch_data['user_id']
        original_channel_id = vouch_data['channel_id']
        original_message_id = vouch_data['message_id']
        
        if approved:
            # Award the point
            add_user_points(guild_id, user_id, 1)
            current_points = get_user_points(guild_id, user_id)
            
            # Send approval message
            embed = discord.Embed(
                title="✅ Vouch Approved!",
                description=f"**{interaction.user.mention}** approved the vouch by <@{user_id}>",
                color=discord.Color.green()
            )
            embed.add_field(name="Points Awarded", value="1 point", inline=True)
            embed.add_field(name="User's Total Points", value=f"{current_points} points", inline=True)
            embed.set_footer(text=f"Approved by {interaction.user.display_name}")
            
            await interaction.response.edit_message(embed=embed, view=None)
            
            # Send confirmation to original channel
            try:
                original_channel = bot.get_channel(int(original_channel_id))
                if original_channel:
                    confirm_embed = discord.Embed(
                        title="🎉 Vouch Approved! 🎉",
                        description=f"Your vouch has been **approved**! You received **1 point**!",
                        color=discord.Color.green()
                    )
                    confirm_embed.add_field(name="Current Points", value=f"**{current_points}** points", inline=False)
                    confirm_embed.set_footer(text="Keep up the good work! 💪")
                    
                    try:
                        original_message = await original_channel.fetch_message(int(original_message_id))
                        await original_message.add_reaction('✅')
                    except:
                        pass
                    
                    await original_channel.send(embed=confirm_embed)
            except Exception as e:
                print(f"Error sending approval confirmation: {e}")
        else:
            # Deny the vouch
            embed = discord.Embed(
                title="❌ Vouch Denied",
                description=f"**{interaction.user.mention}** denied the vouch by <@{user_id}>",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value="Vouch did not meet requirements", inline=False)
            embed.set_footer(text=f"Denied by {interaction.user.display_name}")
            
            await interaction.response.edit_message(embed=embed, view=None)
            
            # Send denial message to original channel
            try:
                original_channel = bot.get_channel(int(original_channel_id))
                if original_channel:
                    deny_embed = discord.Embed(
                        title="❌ Vouch Denied",
                        description="Your vouch has been **denied**. No points were awarded.",
                        color=discord.Color.red()
                    )
                    deny_embed.add_field(name="Reason", value="Vouch did not meet requirements", inline=False)
                    deny_embed.set_footer(text="Please ensure your vouch includes an image and mentions a valid role.")
                    
                    try:
                        original_message = await original_channel.fetch_message(int(original_message_id))
                        await original_message.add_reaction('❌')
                    except:
                        pass
                    
                    await original_channel.send(embed=deny_embed)
            except Exception as e:
                print(f"Error sending denial confirmation: {e}")
        
        # Remove from pending vouches
        del pending_vouches[self.vouch_id]

# Button view for reward redemption
class RewardView(ui.View):
    def __init__(self, user_id, guild_id):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.guild_id = guild_id
        
        # Add buttons for each reward in this guild
        guild_rewards = get_guild_rewards(guild_id)
        for reward_name, reward_info in guild_rewards.items():
            button = RewardButton(reward_name, reward_info['cost'], self.user_id, self.guild_id)
            self.add_item(button)
    
    async def on_timeout(self):
        # Disable all buttons when the view times out
        for item in self.children:
            item.disabled = True

class RewardButton(ui.Button):
    def __init__(self, reward_name, cost, user_id, guild_id):
        self.reward_name = reward_name
        self.cost = cost
        self.user_id = user_id
        self.guild_id = guild_id
        
        # Set button style based on user's points in this guild
        user_points = get_user_points(guild_id, user_id)
        if user_points >= cost:
            style = discord.ButtonStyle.green
            emoji = "🎁"
        else:
            style = discord.ButtonStyle.red
            emoji = "❌"
        
        super().__init__(
            label=f"{reward_name} ({cost} pts)",
            style=style,
            emoji=emoji,
            disabled=(user_points < cost)
        )
    
    async def callback(self, interaction: discord.Interaction):
        # Check if the button user is the same as the command user
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ You can only redeem rewards for yourself! Use `!shop` to see your own rewards.",
                ephemeral=True
            )
            return
        
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild.id)
        user_points = get_user_points(guild_id, user_id)
        
        # Double check if user has enough points
        if user_points < self.cost:
            await interaction.response.send_message(
                f"❌ You need {self.cost} points to redeem '{self.reward_name}' but you only have {user_points} points.",
                ephemeral=True
            )
            return
        
        # Check if reward still exists in this guild
        guild_rewards = get_guild_rewards(guild_id)
        if self.reward_name not in guild_rewards:
            await interaction.response.send_message(
                f"❌ Reward '{self.reward_name}' is no longer available.",
                ephemeral=True
            )
            return
        
        # Deduct points
        set_user_points(guild_id, user_id, user_points - self.cost)
        
        # Send confirmation
        embed = discord.Embed(
            title="🎁 Reward Redeemed! 🎁",
            description=f"**{interaction.user.mention}** successfully redeemed **{self.reward_name}**!",
            color=discord.Color.green()
        )
        remaining_points = get_user_points(guild_id, user_id)
        embed.add_field(name="💰 Cost", value=f"{self.cost} points", inline=True)
        embed.add_field(name="💎 Remaining Points", value=f"{remaining_points} points", inline=True)
        embed.set_footer(text="Please contact an admin to claim your reward!")
        
        await interaction.response.send_message(embed=embed)
        
        # Send a DM to the user
        try:
            dm_embed = discord.Embed(
                title="🎁 Reward Redemption Confirmation",
                description=f"You have successfully redeemed **{self.reward_name}** for {self.cost} points!",
                color=discord.Color.green()
            )
            dm_embed.add_field(name="🏠 Server", value=interaction.guild.name, inline=True)
            dm_embed.add_field(name="💎 Remaining Points", value=f"{remaining_points} points", inline=True)
            dm_embed.set_footer(text="Please contact a server admin to claim your reward!")
            
            await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            pass  # User has DMs disabled
        
        # Send notification to admins
        admin_channel = None
        for channel in interaction.guild.channels:
            if 'admin' in channel.name.lower() or 'staff' in channel.name.lower():
                admin_channel = channel
                break
        
        if admin_channel:
            admin_embed = discord.Embed(
                title="🔔 Reward Redemption Alert",
                description=f"{interaction.user.mention} ({interaction.user.display_name}) redeemed **{self.reward_name}**",
                color=discord.Color.orange()
            )
            admin_embed.add_field(name="💰 Cost", value=f"{self.cost} points", inline=True)
            admin_embed.add_field(name="💎 User's Remaining Points", value=f"{remaining_points} points", inline=True)
            admin_embed.add_field(name="🆔 User ID", value=interaction.user.id, inline=True)
            admin_embed.set_footer(text="Please fulfill this reward request!")
            
            await admin_channel.send(embed=admin_embed)

# Bot status update task
@tasks.loop(minutes=1)
async def status_update():
    try:
        total_guilds = len(bot.guilds)
        total_users = sum(guild.member_count for guild in bot.guilds)
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_guilds} servers | {total_users} users")
        await bot.change_presence(activity=activity)
    except Exception as e:
        print(f"Error updating status: {str(e)}")

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    for guild in bot.guilds:
        print(f'- {guild.name} (id: {guild.id})')
    global points_data, rewards_data, vouch_roles_data, verification_channels
    points_data = load_points()
    rewards_data = load_rewards()
    vouch_roles_data = load_vouch_roles()
    verification_channels = load_verification_channels()
    status_update.start()
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_disconnect():
    print("Bot disconnected from Discord")

@bot.event
async def on_resumed():
    print("Bot resumed connection to Discord")

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"An error occurred: {event}")

@bot.event
async def on_message(message):
    try:
        # Ignore bot messages
        if message.author.bot:
            await bot.process_commands(message)
            return
        
        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        
        # Check if the message is in the vouch channel (including emoji)
        if 'vouch' in message.channel.name.lower():
            print("\n=== Vouch Channel Message ===")
            
            current_time = time.time()
            
            # Check if the message has an image
            has_image = False
            for attachment in message.attachments:
                print(f"Checking attachment: {attachment.filename}")
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    has_image = True
                    print(f"Found image: {attachment.filename}")
                    break
            
            print(f"\nImage check result: {has_image}")
            
            # If image is present, send to verification channel for approval
            if has_image:
                print("\n=== Vouch Detected - Sending for Approval ===")
                
                # Get verification channel
                verification_channel_id = get_verification_channel(guild_id)
                
                if not verification_channel_id:
                    # No verification channel set, send error message
                    embed = discord.Embed(
                        title="⚠️ Verification Channel Not Set",
                        description="A verification channel needs to be set up for vouch approval. Please contact an administrator.",
                        color=discord.Color.orange()
                    )
                    await message.channel.send(embed=embed, delete_after=10)
                    await bot.process_commands(message)
                    return
                
                try:
                    verification_channel = bot.get_channel(int(verification_channel_id))
                    if not verification_channel:
                        print(f"Verification channel {verification_channel_id} not found!")
                        await bot.process_commands(message)
                        return
                    
                    # Get image URL and download image for attachment
                    image_url = None
                    image_attachment = None
                    for attachment in message.attachments:
                        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                            image_url = attachment.url
                            # Download the image to send as attachment
                            try:
                                image_data = await attachment.read()
                                image_attachment = discord.File(
                                    io.BytesIO(image_data),
                                    filename=attachment.filename
                                )
                            except Exception as e:
                                print(f"Error downloading image: {e}")
                            break
                    
                    # Create unique vouch ID
                    vouch_id = f"{guild_id}_{user_id}_{int(current_time)}"
                    
                    # Store pending vouch
                    pending_vouches[vouch_id] = {
                        'guild_id': guild_id,
                        'user_id': user_id,
                        'message_id': str(message.id),
                        'channel_id': str(message.channel.id),
                        'image_url': image_url,
                        'timestamp': current_time
                    }
                    
                    # Create embed for verification channel
                    verify_embed = discord.Embed(
                        title="🔍 Vouch Pending Approval",
                        description=f"New vouch submitted by **{message.author.mention}** ({message.author.display_name})",
                        color=discord.Color.blue()
                    )
                    verify_embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
                    verify_embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
                    verify_embed.add_field(name="Original Message", value=f"[Jump to Message]({message.jump_url})", inline=False)
                    if image_url:
                        verify_embed.set_image(url=image_url)
                    verify_embed.set_footer(text=f"Vouch ID: {vouch_id}")
                    verify_embed.timestamp = message.created_at
                    
                    # Send to verification channel with approve/deny buttons and image attachment
                    view = VouchApprovalView(vouch_id)
                    if image_attachment:
                        await verification_channel.send(embed=verify_embed, view=view, file=image_attachment)
                    else:
                        await verification_channel.send(embed=verify_embed, view=view)
                    
                    # Send confirmation to original channel
                    confirm_embed = discord.Embed(
                        title="⏳ Vouch Submitted for Review",
                        description=f"Your vouch has been submitted for approval! An administrator will review it shortly.",
                        color=discord.Color.blue()
                    )
                    confirm_embed.add_field(name="Status", value="Pending Approval", inline=False)
                    confirm_embed.set_footer(text="You will be notified once your vouch is reviewed.")
                    
                    await message.channel.send(embed=confirm_embed, delete_after=15)
                    await message.add_reaction('⏳')
                    
                    print(f"Vouch sent to verification channel for {message.author.name} in {message.guild.name}")
                except Exception as e:
                    print(f"Error sending vouch to verification channel: {str(e)}")
            else:
                print("\n=== Conditions Not Met ===")
                print(f"- Has image: {has_image}")
                if not has_image:
                    # Send helpful message if no image
                    embed = discord.Embed(
                        title="⚠️ Image Required",
                        description="Please include an image attachment with your vouch!",
                        color=discord.Color.orange()
                    )
                    embed.set_footer(text="Supported formats: PNG, JPG, JPEG, GIF, WEBP")
                    await message.channel.send(embed=embed, delete_after=10)
        
        # Process commands
        await bot.process_commands(message)
    except Exception as e:
        print(f"Error processing message: {str(e)}")

# ======= VOUCH ROLE MANAGEMENT COMMANDS =======
@bot.command(name='addvouchrole')
@commands.has_permissions(administrator=True)
async def add_vouch_role(ctx, role_name: str):
    """Add a role that can be mentioned for vouch points (Admin only)"""
    guild_id = str(ctx.guild.id)
    
    # Ensure guild exists in data
    if guild_id not in vouch_roles_data:
        vouch_roles_data[guild_id] = ["CHEF"]
    
    # Convert to lowercase for comparison but store original case
    existing_roles_lower = [r.lower() for r in vouch_roles_data[guild_id]]
    if role_name.lower() not in existing_roles_lower:
        vouch_roles_data[guild_id].append(role_name)
        save_vouch_roles()
        
        embed = discord.Embed(
            title="✅ Vouch Role Added",
            description=f"Role `{role_name}` has been added to valid vouch roles for this server",
            color=discord.Color.green()
        )
        embed.add_field(name="Server", value=ctx.guild.name, inline=False)
        embed.add_field(name="Guild ID", value=guild_id, inline=False)
    else:
        embed = discord.Embed(
            title="⚠️ Role Already Exists",
            description=f"Role `{role_name}` is already in the valid vouch roles list for this server",
            color=discord.Color.orange()
        )
    
    await ctx.send(embed=embed)

@bot.command(name='removevouchrole')
@commands.has_permissions(administrator=True)
async def remove_vouch_role(ctx, role_name: str):
    """Remove a role from valid vouch roles (Admin only)"""
    guild_id = str(ctx.guild.id)
    
    # Ensure guild exists in data
    if guild_id not in vouch_roles_data:
        vouch_roles_data[guild_id] = ["CHEF"]
    
    # Find and remove the role (case insensitive)
    original_role = None
    for role in vouch_roles_data[guild_id]:
        if role.lower() == role_name.lower():
            original_role = role
            break
    
    if original_role:
        vouch_roles_data[guild_id].remove(original_role)
        
        # Don't allow empty role list - add dev back if list becomes empty
        if not vouch_roles_data[guild_id]:
            vouch_roles_data[guild_id] = ["CHEF"]
        
        save_vouch_roles()
        
        embed = discord.Embed(
            title="✅ Vouch Role Removed",
            description=f"Role `{original_role}` has been removed from valid vouch roles for this server",
            color=discord.Color.green()
        )
        embed.add_field(name="Server", value=ctx.guild.name, inline=False)
        embed.add_field(name="Guild ID", value=guild_id, inline=False)
    else:
        embed = discord.Embed(
            title="❌ Role Not Found",
            description=f"Role `{role_name}` is not in the valid vouch roles list for this server",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)

@bot.command(name='resetvouchroles')
@commands.has_permissions(administrator=True)
async def reset_vouch_roles(ctx):
    """Reset vouch roles to default (dev only) for this server (Admin only)"""
    guild_id = str(ctx.guild.id)
    reset_guild_vouch_roles(guild_id)
    
    embed = discord.Embed(
        title="🔄 Vouch Roles Reset",
        description=f"Vouch roles have been reset to default (`CHEF`) for this server",
        color=discord.Color.blue()
    )
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    embed.add_field(name="Guild ID", value=guild_id, inline=False)
    await ctx.send(embed=embed)

@bot.command(name='listvouchroles')
async def list_vouch_roles(ctx):
    """List all valid vouch roles for this server"""
    guild_id = str(ctx.guild.id)
    valid_roles = get_guild_vouch_roles(guild_id)
    
    embed = discord.Embed(
        title="Valid Vouch Roles",
        description=f"Roles that can be mentioned for vouch points in {ctx.guild.name}",
        color=discord.Color.blue()
    )
    
    if valid_roles:
        roles_text = "\n".join([f"• `{role}`" for role in valid_roles])
        embed.add_field(name="Current Roles", value=roles_text, inline=False)
    else:
        embed.add_field(name="No Roles", value="No valid vouch roles configured!", inline=False)
    
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    embed.add_field(name="Guild ID", value=guild_id, inline=False)
    embed.set_footer(text="Use !addvouchrole <role> to add a new role (Admin only)")
    await ctx.send(embed=embed)

@bot.command(name='setverifychannel')
@commands.has_permissions(administrator=True)
async def set_verify_channel(ctx, channel: discord.TextChannel = None):
    """Set the verification channel for vouch approval (Admin only)"""
    if channel is None:
        channel = ctx.channel
    
    guild_id = str(ctx.guild.id)
    set_verification_channel(guild_id, channel.id)
    
    embed = discord.Embed(
        title="✅ Verification Channel Set",
        description=f"Vouch verification channel has been set to {channel.mention}",
        color=discord.Color.green()
    )
    embed.add_field(name="Channel", value=f"{channel.mention} ({channel.name})", inline=False)
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    embed.set_footer(text="All vouches will now be sent here for approval")
    await ctx.send(embed=embed)

@bot.command(name='getverifychannel')
async def get_verify_channel(ctx):
    """Get the current verification channel"""
    guild_id = str(ctx.guild.id)
    channel_id = get_verification_channel(guild_id)
    
    if not channel_id:
        embed = discord.Embed(
            title="⚠️ No Verification Channel Set",
            description="No verification channel has been set for this server.",
            color=discord.Color.orange()
        )
        embed.add_field(name="How to Set", value="Use `!setverifychannel #channel` (Admin only)", inline=False)
        await ctx.send(embed=embed)
        return
    
    try:
        channel = bot.get_channel(int(channel_id))
        if channel:
            embed = discord.Embed(
                title="📋 Verification Channel",
                description=f"Current verification channel: {channel.mention}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Channel", value=f"{channel.mention} ({channel.name})", inline=False)
            embed.add_field(name="Channel ID", value=str(channel_id), inline=False)
        else:
            embed = discord.Embed(
                title="⚠️ Channel Not Found",
                description=f"The verification channel (ID: {channel_id}) no longer exists.",
                color=discord.Color.red()
            )
            embed.add_field(name="How to Fix", value="Use `!setverifychannel #channel` to set a new one (Admin only)", inline=False)
    except:
        embed = discord.Embed(
            title="⚠️ Error",
            description="Could not retrieve verification channel information.",
            color=discord.Color.red()
        )
    
    await ctx.send(embed=embed)

# ======= SLASH COMMANDS =======
@bot.tree.command(name="thank", description="Thank a customer and guide them to the vouch channel")
@app_commands.describe(member="The customer to thank")
async def thank_command(interaction: discord.Interaction, member: discord.Member):
    """Thank a customer for choosing the server and guide them to vouch"""
    
    # Find vouch channel
    vouch_channel = None
    for channel in interaction.guild.text_channels:
        if 'vouch' in channel.name.lower():
            vouch_channel = channel
            break
    
    embed = discord.Embed(
        title="🙏 Thank You!",
        description=f"Thank you **{member.mention}** for choosing **{interaction.guild.name}**!",
        color=discord.Color.green()
    )
    
    if vouch_channel:
        embed.add_field(
            name="📸 Leave a Vouch!",
            value=f"After receiving your order, please head to {vouch_channel.mention} and post a vouch with an image!\n\n**How to vouch:**\n1. Go to {vouch_channel.mention}\n2. Post a message with an image of your order\n3. An admin will review and approve it\n4. Earn points for your vouch!",
            inline=False
        )
    else:
        embed.add_field(
            name="📸 Leave a Vouch!",
            value="After receiving your order, please post a vouch in a channel with 'vouch' in the name!\n\n**How to vouch:**\n1. Find a channel with 'vouch' in the name\n2. Post a message with an image of your order\n3. An admin will review and approve it\n4. Earn points for your vouch!",
            inline=False
        )
    
    embed.set_footer(text="We appreciate your support! 💙")
    embed.timestamp = interaction.created_at
    
    await interaction.response.send_message(embed=embed)

# ======= POINTS COMMANDS =======
@bot.command(name='points')
async def check_points(ctx, member: discord.Member = None):
    """Check points for a user. If no user is specified, check your own points."""
    if member is None:
        member = ctx.author
    
    points = get_user_points(ctx.guild.id, member.id)
    
    embed = discord.Embed(
        title="💎 Points System",
        description=f"{member.display_name}'s Points",
        color=discord.Color.blue()
    )
    embed.add_field(name="Points", value=f"**{points}** points", inline=False)
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    await ctx.send(embed=embed)

@bot.command(name='addpoints')
@commands.has_permissions(administrator=True)
async def add_points_cmd(ctx, member: discord.Member, amount: int):
    """Add points to a user (Admin only)"""
    if amount <= 0:
        await ctx.send("Please provide a positive number of points.")
        return
    
    add_user_points(ctx.guild.id, member.id, amount)
    new_total = get_user_points(ctx.guild.id, member.id)
    
    embed = discord.Embed(
        title="✅ Points Added",
        description=f"Added {amount} points to {member.display_name}",
        color=discord.Color.green()
    )
    embed.add_field(name="New Total", value=f"**{new_total}** points", inline=False)
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    await ctx.send(embed=embed)

@bot.command(name='removepoints')
@commands.has_permissions(administrator=True)
async def remove_points_cmd(ctx, member: discord.Member, amount: int):
    """Remove points from a user (Admin only)"""
    if amount <= 0:
        await ctx.send("Please provide a positive number of points.")
        return
    
    current_points = get_user_points(ctx.guild.id, member.id)
    new_points = max(0, current_points - amount)
    set_user_points(ctx.guild.id, member.id, new_points)
    
    embed = discord.Embed(
        title="❌ Points Removed",
        description=f"Removed {amount} points from {member.display_name}",
        color=discord.Color.red()
    )
    embed.add_field(name="New Total", value=f"**{new_points}** points", inline=False)
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    await ctx.send(embed=embed)

@bot.command(name='leaderboard')
async def show_leaderboard(ctx):
    """Show the top 10 users with the most points in this server"""
    guild_points = get_guild_points(ctx.guild.id)
    sorted_users = sorted(guild_points.items(), key=lambda x: x[1], reverse=True)[:10]
    
    embed = discord.Embed(
        title="🏆 Points Leaderboard",
        description=f"Top users in {ctx.guild.name}",
        color=discord.Color.gold()
    )
    
    if not sorted_users:
        embed.add_field(name="No Data", value="No points have been awarded in this server yet!", inline=False)
    else:
        for i, (user_id, points) in enumerate(sorted_users, 1):
            try:
                user = await bot.fetch_user(int(user_id))
                embed.add_field(
                    name=f"{i}. {user.display_name}",
                    value=f"**{points}** points",
                    inline=False
                )
            except:
                embed.add_field(
                    name=f"{i}. Unknown User",
                    value=f"**{points}** points",
                    inline=False
                )
    
    await ctx.send(embed=embed)

# ======= REWARDS SYSTEM COMMANDS =======
@bot.command(name='addreward')
@commands.has_permissions(administrator=True)
async def add_reward(ctx, name: str, cost: int):
    """Add a reward (Admin only). Usage: !addreward name cost"""
    if cost <= 0:
        await ctx.send("Please provide a positive cost for the reward.")
        return
    
    guild_id = str(ctx.guild.id)
    guild_rewards = get_guild_rewards(guild_id)
    guild_rewards[name] = {
        'cost': cost,
        'name': name
    }
    save_rewards()
    
    embed = discord.Embed(
        title="✅ Reward Added",
        description=f"**{name}** - {cost} points",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Server: {ctx.guild.name}")
    await ctx.send(embed=embed)

@bot.command(name='removereward')
@commands.has_permissions(administrator=True)
async def remove_reward(ctx, name: str):
    """Remove a reward (Admin only)"""
    guild_id = str(ctx.guild.id)
    guild_rewards = get_guild_rewards(guild_id)
    
    if name not in guild_rewards:
        await ctx.send(f"Reward '{name}' not found in this server.")
        return
    
    del guild_rewards[name]
    save_rewards()
    
    embed = discord.Embed(
        title="❌ Reward Removed",
        description=f"Reward '{name}' has been removed.",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Server: {ctx.guild.name}")
    await ctx.send(embed=embed)

@bot.command(name='rewards')
async def show_rewards(ctx):
    """Show all available rewards in this server"""
    guild_id = str(ctx.guild.id)
    guild_rewards = get_guild_rewards(guild_id)
    
    if not guild_rewards:
        embed = discord.Embed(
            title="🛍️ No Rewards Available",
            description="There are currently no rewards available in this server.",
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Server: {ctx.guild.name}")
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🏪 Available Rewards",
        description=f"Use `!redeem <reward_name>` to redeem a reward or `!shop` for interactive buttons\n\n**Server:** {ctx.guild.name}",
        color=discord.Color.blue()
    )
    
    for reward_name, reward_info in guild_rewards.items():
        embed.add_field(
            name=f"🎁 {reward_name}",
            value=f"💎 {reward_info['cost']} points",
            inline=True
        )
    
    await ctx.send(embed=embed)

@bot.command(name='shop')
async def interactive_shop(ctx):
    """Interactive reward shop with buttons"""
    guild_id = str(ctx.guild.id)
    guild_rewards = get_guild_rewards(guild_id)
    
    if not guild_rewards:
        embed = discord.Embed(
            title="🛍️ Shop Closed",
            description="There are currently no rewards available in this server.",
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Server: {ctx.guild.name}")
        await ctx.send(embed=embed)
        return
    
    user_id = str(ctx.author.id)
    user_points = get_user_points(guild_id, user_id)
    
    embed = discord.Embed(
        title="🏪 Interactive Reward Shop",
        description=f"Welcome {ctx.author.display_name}! Click the buttons below to redeem rewards.\n\n**Server:** {ctx.guild.name}",
        color=discord.Color.purple()
    )
    embed.add_field(name="💎 Your Points", value=f"**{user_points}** points", inline=True)
    embed.add_field(name="🕒 Time Limit", value="5 minutes", inline=True)
    embed.add_field(name="ℹ️ How it works", value="🟢 Green = Can afford\n🔴 Red = Can't afford", inline=False)
    
    # Add reward information
    reward_list = ""
    for reward_name, reward_info in guild_rewards.items():
        cost = reward_info['cost']
        if user_points >= cost:
            status = "✅"
        else:
            status = "❌"
        reward_list += f"{status} **{reward_name}** - {cost} points\n"
    
    embed.add_field(name="🎁 Available Rewards", value=reward_list, inline=False)
    embed.set_footer(text="Buttons will be disabled after 5 minutes of inactivity")
    
    view = RewardView(ctx.author.id, guild_id)
    await ctx.send(embed=embed, view=view)

@bot.command(name='redeem')
async def redeem_reward(ctx, *, reward_name: str):
    """Redeem a reward using points"""
    user_id = str(ctx.author.id)
    guild_id = str(ctx.guild.id)
    guild_rewards = get_guild_rewards(guild_id)
    user_points = get_user_points(guild_id, user_id)
    
    if reward_name not in guild_rewards:
        await ctx.send(f"Reward '{reward_name}' not found in this server. Use `!rewards` to see available rewards.")
        return
    
    reward_cost = guild_rewards[reward_name]['cost']
    
    if user_points < reward_cost:
        embed = discord.Embed(
            title="❌ Insufficient Points",
            description=f"You need {reward_cost} points to redeem '{reward_name}' but you only have {user_points} points.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    # Deduct points
    set_user_points(guild_id, user_id, user_points - reward_cost)
    
    # Send confirmation to user
    remaining_points = get_user_points(guild_id, user_id)
    embed = discord.Embed(
        title="🎁 Reward Redeemed! 🎁",
        description=f"**{ctx.author.mention}** successfully redeemed **{reward_name}**!",
        color=discord.Color.green()
    )
    embed.add_field(name="💰 Cost", value=f"{reward_cost} points", inline=True)
    embed.add_field(name="💎 Remaining Points", value=f"{remaining_points} points", inline=True)
    embed.set_footer(text="Please contact an admin to claim your reward!")
    
    await ctx.send(embed=embed)
    
    # Send a DM to the user
    try:
        dm_embed = discord.Embed(
            title="🎁 Reward Redemption Confirmation",
            description=f"You have successfully redeemed **{reward_name}** for {reward_cost} points!",
            color=discord.Color.green()
        )
        dm_embed.add_field(name="🏠 Server", value=ctx.guild.name, inline=True)
        dm_embed.add_field(name="💎 Remaining Points", value=f"{remaining_points} points", inline=True)
        dm_embed.set_footer(text="Please contact a server admin to claim your reward!")
        
        await ctx.author.send(embed=dm_embed)
    except discord.Forbidden:
        pass  # User has DMs disabled
    
    # Send notification to admins
    admin_channel = None
    for channel in ctx.guild.channels:
        if 'admin' in channel.name.lower() or 'staff' in channel.name.lower():
            admin_channel = channel
            break
    
    if admin_channel:
        admin_embed = discord.Embed(
            title="🔔 Reward Redemption Alert",
            description=f"{ctx.author.mention} ({ctx.author.display_name}) redeemed **{reward_name}**",
            color=discord.Color.orange()
        )
        admin_embed.add_field(name="💰 Cost", value=f"{reward_cost} points", inline=True)
        admin_embed.add_field(name="💎 User's Remaining Points", value=f"{remaining_points} points", inline=True)
        admin_embed.add_field(name="🆔 User ID", value=ctx.author.id, inline=True)
        try:
            await admin_channel.send(embed=admin_embed)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to send in admin channel

# ======= HELP COMMAND =======
@bot.command(name='commands')
async def show_commands(ctx):
    """Show all available commands"""
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Here are all available commands for this server:",
        color=discord.Color.purple()
    )
    
    # Points Commands
    embed.add_field(
        name="📊 Points Commands",
        value="`!points [user]` - Check points\n`!leaderboard` - Show top users\n`!addpoints <user> <amount>` - Add points (Admin)\n`!removepoints <user> <amount>` - Remove points (Admin)",
        inline=False
    )
    
    # Vouch Role Commands
    embed.add_field(
        name="🎭 Vouch Role Commands",
        value="`!listvouchroles` - List valid vouch roles\n`!addvouchrole <role>` - Add vouch role (Admin)\n`!removevouchrole <role>` - Remove vouch role (Admin)\n`!resetvouchroles` - Reset to default (Admin)",
        inline=False
    )
    
    # Verification Commands
    embed.add_field(
        name="🔍 Verification Commands",
        value="`!setverifychannel [#channel]` - Set verification channel (Admin)\n`!getverifychannel` - Get current verification channel",
        inline=False
    )
    
    # Rewards Commands
    embed.add_field(
        name="🏪 Rewards Commands",
        value="`!rewards` - Show available rewards\n`!shop` - Interactive reward shop\n`!redeem <reward>` - Redeem a reward\n`!addreward <name> <cost>` - Add reward (Admin)\n`!removereward <name>` - Remove reward (Admin)",
        inline=False
    )
    
    # How Vouching Works
    embed.add_field(
        name="✅ How Vouching Works",
        value="1. Post in a channel with 'vouch' in the name\n2. Include an image attachment\n3. Vouch is sent for **admin approval**\n4. Earn 1 point when **approved**!",
        inline=False
    )
    
    embed.add_field(name="Server", value=ctx.guild.name, inline=False)
    embed.set_footer(text="Each server has independent points, roles, and rewards!")
    await ctx.send(embed=embed)

# Run the bot
bot.run(TOKEN, reconnect=True) 
