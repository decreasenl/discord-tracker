import asyncio
import json
import logging
import time
import discord
from discord import app_commands
from discord_tracker.config import bootstrap_settings
from discord_tracker.bot.permissions import permitted
from discord_tracker.integrations.wise_old_man import WiseOldMan, IntegrationError
from discord_tracker.services.members import Members
from discord_tracker.services.competitions import Competitions
from discord_tracker.jobs.competitions import CompetitionWorker
from discord_tracker.services.alerts import Alerts
from discord_tracker.rank_rules import RankRules
from discord_tracker.services.ranks import Ranks
from discord_tracker.jobs.ranks import RankWorker

log = logging.getLogger(__name__)


def player_summary(player):
    snapshot = player.get("latestSnapshot") or {}
    overall = ((snapshot.get("data") or {}).get("skills") or {}).get("overall") or {}
    return (f"Player: {discord.utils.escape_markdown(player['username'])}\n"
            f"Total level: {overall.get('level', 'unavailable')}\n"
            f"Total XP: {overall.get('experience', player.get('exp', 'unavailable'))}\n"
            f"Upstream updated: {player.get('updatedAt') or 'unknown'}")


class Tracker(discord.Client):
    def __init__(self, config, db):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config, self.db = config, db
        self.settings = db.settings()
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.command_error
        rules = RankRules.load()
        self.wom = WiseOldMan()
        self.ranks = Ranks(db, self.wom, rules)
        self.rank_worker = RankWorker(self)
        self.rank_task = None
        self.members_service = Members(db, self.wom)
        self.heartbeat = None
        self.ready_for_commands = False
        self.diagnostic_times = {}
        self.competitions = Competitions(db)
        self.competition_worker = CompetitionWorker(self)
        self.alerts = Alerts(self)
        self.competition_task = None

    async def command_error(self, interaction, error):
        # Includes parameter conversion errors raised before the command callback.
        log.error('Discord command dispatch failed (%s)', type(error).__name__)
        self.db.audit(interaction.user.id, 'command-dispatch-error', interaction.guild_id, type(error).__name__)
        try:
            message = 'Command could not complete. Check its parameters; managers can inspect /audit.'
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.error('Unable to deliver command error response')

    async def setup_hook(self):
        candidate = self.settings or bootstrap_settings()
        guild_id = int(candidate['DISCORD_GUILD_ID'])
        # REST validation works before connecting to the Gateway.
        guild = await self.fetch_guild(guild_id)
        roles = {role.id: role for role in await guild.fetch_roles()}
        for key in ('DISCORD_BOT_ACCESS_ROLE_ID', 'DISCORD_MANAGER_ROLE_ID'):
            role = roles.get(int(candidate[key]))
            if role is None or role.is_default() or role.managed:
                raise ValueError(f'{key} must reference an assignable role in the configured server')
        self.db.seed(candidate)
        self.settings = self.db.settings()
        self.register_commands(discord.Object(id=guild_id))
        await self.tree.sync(guild=discord.Object(id=guild_id))
        self.ready_for_commands = True
        self.heartbeat = asyncio.create_task(self.write_health())
        self.competition_task = asyncio.create_task(self.competition_worker.run())
        self.rank_task = asyncio.create_task(self.rank_worker.run())

    async def authorize(self, interaction, manager=False):
        if not self.ready_for_commands or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Bot unavailable or command outside the configured server.', ephemeral=True)
            return False
        if not permitted(self.settings, interaction.guild_id, [r.id for r in interaction.user.roles], manager):
            await interaction.response.send_message('Your Discord role does not permit this command.', ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.db.display_name(interaction.user.id, interaction.user.display_name)
        return True

    def register_commands(self, guild):
        async def reply(interaction, operation):
            command = interaction.command.name if interaction.command else 'unknown'
            outcome = 'completed'
            try:
                message = await operation()
            except (ValueError, IntegrationError) as exc:
                outcome = 'failed'
                message = f'Could not complete: {exc}'
            except Exception as exc:
                # Do not log arbitrary exception payloads: HTTP errors can contain tokens.
                log.error('Command failed: %s', type(exc).__name__)
                outcome = 'failed'
                message = 'Operation failed. An operator can inspect the service logs.'
                self.alerts.enqueue(f'Command /{command} failed unexpectedly ({type(exc).__name__}). Inspect operator logs and /audit.')
            if 'PARTIAL:' in message:
                outcome = 'partial'
            self.db.audit(interaction.user.id, f'command:{command}', interaction.guild_id, outcome)
            log.info('Command %s actor=%s outcome=%s', command, interaction.user.id, outcome)
            try:
                await interaction.followup.send(message[:1900], ephemeral=True)
            except discord.HTTPException:
                self.db.audit(interaction.user.id, f'reply:{command}', interaction.guild_id, 'delivery-failed')
                self.alerts.enqueue(f'Command /{command} finished with outcome {outcome}, but its private reply could not be delivered. Check /audit before repeating the action.')

        from discord_tracker.bot.management import register
        register(self, guild, reply)
        from discord_tracker.bot.events import register as register_events
        register_events(self, guild, reply)
        from discord_tracker.bot.ranks import register as register_ranks
        register_ranks(self, guild, reply)

        @self.tree.command(name='status', description='Check bot readiness', guild=guild)
        async def status(interaction: discord.Interaction):
            if await self.authorize(interaction):
                await interaction.followup.send('Bot is connected; SQLite is available.', ephemeral=True)

        @self.tree.command(name='link', description='Manager: link a Discord member to a tracked OSRS player', guild=guild)
        async def link(interaction: discord.Interaction, member: discord.Member, osrs_name: str):
            if not await self.authorize(interaction, True):
                return
            async def operation():
                if member.bot:
                    raise ValueError('Bot accounts cannot be linked')
                if not 1 <= len(osrs_name.strip()) <= 12:
                    raise ValueError('OSRS names must contain 1–12 characters')
                player = await self.members_service.link(interaction.user.id, member.id, member.display_name, osrs_name.strip())
                return f'Linked Discord user {member.id}.\n{player_summary(player)}'
            await reply(interaction, operation)

        @self.tree.command(name='stats', description='Retrieve statistics for your linked OSRS account', guild=guild)
        async def stats(interaction: discord.Interaction):
            if not await self.authorize(interaction):
                return
            async def operation():
                return player_summary(await self.members_service.retrieve(interaction.user.id, interaction.user.id))
            await reply(interaction, operation)

        @self.tree.command(name='member', description='Manager: retrieve a linked member and their statistics', guild=guild)
        async def member_info(interaction: discord.Interaction, member: discord.Member):
            if not await self.authorize(interaction, True):
                return
            async def operation():
                result = await self.members_service.retrieve(interaction.user.id, member.id)
                return f'Discord user: {member.id}\n{player_summary(result)}\nRetrieved: {self.db.member(member.id)["retrieved_at"]}'
            await reply(interaction, operation)

        @self.tree.command(name='refresh', description='Manager: request an upstream OSRS player update', guild=guild)
        async def refresh(interaction: discord.Interaction, member: discord.Member):
            if not await self.authorize(interaction, True):
                return
            async def operation():
                result = await self.members_service.retrieve(interaction.user.id, member.id, refresh=True)
                return f'Refresh completed.\n{player_summary(result)}'
            await reply(interaction, operation)

        @self.tree.command(name='diagnostics', description='Manager: test Discord or Wise Old Man independently', guild=guild)
        @app_commands.choices(integration=[app_commands.Choice(name='Discord', value='discord'),
                                          app_commands.Choice(name='Wise Old Man', value='wom')])
        async def diagnostics(interaction: discord.Interaction, integration: app_commands.Choice[str], osrs_name: str | None = None):
            if not await self.authorize(interaction, True):
                return
            async def operation():
                key = (interaction.user.id, integration.value)
                if time.monotonic() - self.diagnostic_times.get(key, float('-inf')) < 10:
                    raise ValueError('Wait 10 seconds between diagnostic checks')
                self.diagnostic_times[key] = time.monotonic()
                try:
                    if integration.value == 'discord':
                        await self.fetch_guild(interaction.guild_id)
                        bot_member = interaction.guild.me
                        permissions = interaction.channel.permissions_for(bot_member)
                        message = (f'Discord REST and Gateway available.\nCurrent channel: '
                                   f'view={permissions.view_channel}, send={permissions.send_messages}.\n'
                                   'Read-only check; message delivery was not tested.')
                    elif osrs_name:
                        message = 'Wise Old Man player lookup succeeded.\n' + player_summary(await self.wom.player(username=osrs_name))
                    elif self.settings.get('WOM_GROUP_ID'):
                        group = await self.wom.request('GET', f"groups/{self.settings['WOM_GROUP_ID']}")
                        message = f"Wise Old Man group lookup succeeded (ID {group.get('id')})."
                    else:
                        raise ValueError('Supply osrs_name or configure WOM_GROUP_ID during initial setup')
                except Exception:
                    self.db.audit(interaction.user.id, 'diagnostics', integration.value, 'failed')
                    raise
                self.db.audit(interaction.user.id, 'diagnostics', integration.value, 'completed')
                return message
            await reply(interaction, operation)

    async def write_health(self):
        path = self.config.database.parent / 'health.json'
        while True:
            self.db.connection.execute('SELECT 1')
            worker_alive = self.competition_task is not None and not self.competition_task.done()
            worker_alive = worker_alive and self.rank_task is not None and not self.rank_task.done()
            path.write_text(json.dumps({'at': time.time(), 'connected': self.is_ready() and worker_alive}))
            await asyncio.sleep(10)

    async def close(self):
        self.ready_for_commands = False
        if self.rank_task:
            self.rank_task.cancel()
            await asyncio.gather(self.rank_task, return_exceptions=True)
        if self.competition_task:
            self.competition_task.cancel()
            await asyncio.gather(self.competition_task, return_exceptions=True)
        if self.heartbeat:
            self.heartbeat.cancel()
            await asyncio.gather(self.heartbeat, return_exceptions=True)
        tasks = list(self.members_service.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.wom.close()
        await super().close()
        (self.config.database.parent / 'health.json').unlink(missing_ok=True)
