"""Manager commands; all responses use the bot's shared authorization boundary."""
import discord
from discord import app_commands
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def register(bot, guild, reply):
    @bot.tree.command(name='set-alert-channel', description='Manager: configure the channel for operational alerts', guild=guild)
    async def set_alert_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            permissions = channel.permissions_for(interaction.guild.me)
            if channel.guild.id != interaction.guild_id or not (permissions.view_channel and permissions.send_messages):
                raise ValueError('Choose a channel in this server where the bot can view and send messages')
            bot.db.setting(interaction.user.id, 'ALERT_CHANNEL_ID', str(channel.id))
            bot.settings = bot.db.settings()
            return 'Alert channel saved. Pending alerts will be delivered there; use /test-alert to verify delivery.'
        await reply(interaction, operation)

    @bot.tree.command(name='test-alert', description='Manager: queue a test notification to the configured alert channel', guild=guild)
    async def test_alert(interaction: discord.Interaction):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if not bot.db.settings().get('ALERT_CHANNEL_ID'):
                raise ValueError('Configure /set-alert-channel first')
            bot.alerts.enqueue(f'Test notification requested by Discord user {interaction.user.id}. Alert delivery is working.')
            return 'Test notification queued. It will appear in the alert channel when delivery succeeds.'
        await reply(interaction, operation)

    @bot.tree.command(name='archive', description='Manager: archive a member without deleting history', guild=guild)
    async def archive(interaction: discord.Interaction, discord_id: str, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if not discord_id.isdecimal():
                raise ValueError('Provide a numeric Discord user ID')
            bot.db.change_member(interaction.user.id, discord_id, 'archive', reason)
            return 'Member archived. Their links and history are retained.'
        await reply(interaction, operation)

    @bot.tree.command(name='restore', description='Manager: restore an archived member', guild=guild)
    async def restore(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            bot.db.change_member(interaction.user.id, member.id, 'restore', reason)
            return 'Member restored.'
        await reply(interaction, operation)

    @bot.tree.command(name='relink', description='Manager: correct a member account link and preserve history', guild=guild)
    async def relink(interaction: discord.Interaction, member: discord.Member, osrs_name: str, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if member.bot:
                raise ValueError('Bot accounts cannot be linked')
            await bot.members_service.relink(interaction.user.id, member.id, osrs_name, reason)
            return 'Account link replaced; the previous member record is preserved in history.'
        await reply(interaction, operation)

    @bot.tree.command(name='settings', description='Manager: view non-secret community settings', guild=guild)
    async def settings(interaction: discord.Interaction):
        if await bot.authorize(interaction, True):
            await interaction.followup.send('\n'.join(f'{key}: {value}' for key, value in bot.settings.items())[:1900], ephemeral=True)

    @bot.tree.command(name='set-role', description='Manager: set an access role', guild=guild)
    @app_commands.choices(purpose=[app_commands.Choice(name='Bot access', value='DISCORD_BOT_ACCESS_ROLE_ID'),
                                  app_commands.Choice(name='Manager', value='DISCORD_MANAGER_ROLE_ID')])
    async def set_role(interaction: discord.Interaction, purpose: app_commands.Choice[str], role: discord.Role):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if role.guild.id != interaction.guild_id or role.is_default() or role.managed:
                raise ValueError('Choose an ordinary role from this server')
            # Avoid accidental lockout; recovery remains available to the host operator.
            if purpose.value == 'DISCORD_MANAGER_ROLE_ID' and role not in interaction.user.roles:
                raise ValueError('Assign yourself the new manager role before selecting it')
            bot.db.setting(interaction.user.id, purpose.value, str(role.id))
            bot.settings = bot.db.settings()
            return 'Access role updated. The next command uses the new setting.'
        await reply(interaction, operation)

    @bot.tree.command(name='set-timezone', description='Manager: change the timezone for future periods', guild=guild)
    async def set_timezone(interaction: discord.Interaction, timezone: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('Use a valid IANA timezone, such as Europe/Amsterdam') from None
            bot.db.setting(interaction.user.id, 'COMMUNITY_TIMEZONE', timezone)
            bot.settings = bot.db.settings()
            return 'Timezone updated; existing timestamps are unchanged.'
        await reply(interaction, operation)

    @bot.tree.command(name='set-group', description='Manager: select a Wise Old Man group', guild=guild)
    async def set_group(interaction: discord.Interaction, group_id: app_commands.Range[int, 1]):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            await bot.wom.request('GET', f'groups/{group_id}')
            bot.db.setting(interaction.user.id, 'WOM_GROUP_ID', str(group_id))
            bot.settings = bot.db.settings()
            return 'Wise Old Man group validated and saved.'
        await reply(interaction, operation)

    @bot.tree.command(name='audit', description='Manager: inspect recent management actions', guild=guild)
    async def audit(interaction: discord.Interaction):
        if await bot.authorize(interaction, True):
            rows = bot.db.connection.execute('SELECT at,actor,action,target,outcome FROM audit ORDER BY id DESC LIMIT 8').fetchall()
            text = '\n'.join(f'{r["at"]} | {r["actor"]} | {r["action"]} {r["target"]}: {r["outcome"]}' for r in rows)
            await interaction.followup.send(text[:1900] or 'No audit entries yet.', ephemeral=True)
