import json
import os
import time
from datetime import datetime
import discord
from discord import app_commands
from discord_tracker.services.event_operations import matches


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.timestamp()
    except ValueError:
        raise ValueError('Use ISO timestamps including offset, such as 2026-10-01T18:00:00+02:00') from None


def register(bot, guild, reply):
    kinds = [app_commands.Choice(name='Skill of the Week', value='skill'), app_commands.Choice(name='Boss of the Week', value='boss')]

    @bot.tree.command(name='event-config', description='Manager: configure eligible metrics and cooldown', guild=guild)
    @app_commands.choices(kind=kinds)
    async def configure(interaction: discord.Interaction, kind: app_commands.Choice[str], metrics: str,
                        choices: app_commands.Range[int, 2, 10] = 3, cooldown_months: app_commands.Range[int, 0, 120] = 12):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            bot.competitions.configure(interaction.user.id, kind.value, metrics, choices, cooldown_months)
            bot.settings = bot.db.settings()
            return 'Rotation saved. Changes apply when future polls open.'
        await reply(interaction, operation)

    @bot.tree.command(name='event-schedule', description='Manager: schedule a poll and competition using offset-aware timestamps', guild=guild)
    @app_commands.choices(kind=kinds)
    async def schedule(interaction: discord.Interaction, kind: app_commands.Choice[str], channel: discord.TextChannel,
                       poll_open: str, poll_close: str, starts: str, ends: str, repeat_weekly: bool = False):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if not os.getenv('WOM_GROUP_VERIFICATION_CODE'):
                raise ValueError('Operator must supply WOM_GROUP_VERIFICATION_CODE before scheduling')
            permissions = channel.permissions_for(interaction.guild.me)
            if channel.guild.id != interaction.guild_id or not all((permissions.view_channel, permissions.send_messages, permissions.read_message_history)):
                raise ValueError('Bot needs View Channel, Send Messages and Read Message History in this server channel')
            event_id = bot.competitions.schedule(interaction.user.id, kind.value, channel.id,
                                                 *map(timestamp, (poll_open, poll_close, starts, ends)), repeat_weekly)
            return f'Event #{event_id} scheduled. The worker checks every 15 seconds.'
        await reply(interaction, operation)

    @bot.tree.command(name='vote', description='Cast or change your vote in an open event poll', guild=guild)
    async def vote(interaction: discord.Interaction, event_id: int, choice: int):
        if not await bot.authorize(interaction):
            return
        async def operation():
            selected = bot.competitions.vote(event_id, interaction.user.id, choice)
            return f'Your vote is now {selected}.'
        await reply(interaction, operation)

    @bot.tree.command(name='competition', description='View an event and its poll options', guild=guild)
    async def competition(interaction: discord.Interaction, event_id: int):
        if not await bot.authorize(interaction):
            return
        async def operation():
            event = bot.competitions.get(event_id)
            text = f"Event #{event_id}: {event['kind']} / {event['state']}\n" + '\n'.join(f'{i}. {m}' for i, m in enumerate(json.loads(event['options']), 1))
            if event['competition_id']:
                text += f"\nhttps://wiseoldman.net/competitions/{event['competition_id']}"
            return text
        await reply(interaction, operation)

    @bot.tree.command(name='events', description='Manager: view recent events and failures', guild=guild)
    async def events(interaction: discord.Interaction):
        if await bot.authorize(interaction, True):
            rows = bot.db.connection.execute('SELECT id,kind,state,error,deletion_state FROM events ORDER BY id DESC LIMIT 10').fetchall()
            text = '\n'.join(f"#{r['id']} {r['kind']}: {r['state']} deletion={r['deletion_state'] or 'not requested'} {r['error'] or ''}" for r in rows)
            await interaction.followup.send(text[:1900] or 'No events yet.', ephemeral=True)

    @bot.tree.command(name='event-cancel', description='Manager: cancel automation, optionally confirming irreversible WOM deletion', guild=guild)
    async def cancel(interaction: discord.Interaction, event_id: int, reason: str,
                     delete_remote: bool = False, confirm_competition_id: int | None = None):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            async with bot.competition_worker.lock:
                return await bot.competition_worker.operations.cancel(event_id, interaction.user.id, reason,
                                                                      delete_remote, confirm_competition_id)
        await reply(interaction, operation)

    @bot.tree.command(name='event-retry', description='Manager: resume an event after reviewing external state', guild=guild)
    async def retry(interaction: discord.Interaction, event_id: int, confirmed_no_external_creation: bool = False):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            async with bot.competition_worker.lock:
                event = bot.competitions.get(event_id)
                if event['state'] != 'attention_required':
                    raise ValueError('Only an event requiring attention can be retried')
                if event['competition_id']:
                    state = 'ready'
                elif event['winner']:
                    if not confirmed_no_external_creation:
                        raise ValueError('Inspect Wise Old Man first. Attach an existing competition with /event-reconcile, or confirm none was created before retrying')
                    if time.time() >= event['starts']:
                        raise ValueError('Start time passed; cancel and schedule a replacement')
                    state = 'selected'
                elif event['message_id']:
                    if time.time() >= event['starts']:
                        raise ValueError('Start time passed; cancel and schedule a replacement')
                    state = 'voting'
                else:
                    if not confirmed_no_external_creation:
                        raise ValueError('Inspect the Discord channel and confirm no poll was posted before retrying')
                    if time.time() >= event['closes']:
                        raise ValueError('Poll window passed; cancel and schedule a replacement')
                    state = 'scheduled'
                bot.competitions.state(event_id, state)
                bot.db.audit(interaction.user.id, 'event-retry', event_id, f'{state}; external creation absent confirmed={confirmed_no_external_creation}')
                return 'Event queued for processing.'
        await reply(interaction, operation)

    @bot.tree.command(name='event-winner', description='Manager: select a recorded option when a poll needs intervention', guild=guild)
    async def winner(interaction: discord.Interaction, event_id: int, metric: str, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            async with bot.competition_worker.lock:
                event = bot.competitions.get(event_id)
                if event['winner'] or event['state'] not in ('voting', 'attention_required') or time.time() >= event['starts']:
                    raise ValueError('Only an unresolved poll before the competition start can be overridden')
                if not reason.strip():
                    raise ValueError('A reason is required')
                bot.competitions.choose(event_id, interaction.user.id, metric)
                bot.db.audit(interaction.user.id, 'override-reason', event_id, reason[:500])
                return 'Winner selected. Competition creation will follow.'
        await reply(interaction, operation)

    @bot.tree.command(name='event-reconcile', description='Manager: attach an existing WOM competition after an uncertain create', guild=guild)
    async def reconcile(interaction: discord.Interaction, event_id: int, competition_id: int):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            async with bot.competition_worker.lock:
                event = bot.competitions.get(event_id)
                if event['state'] not in ('attention_required', 'reconciling', 'cancelled') or not event['winner']:
                    raise ValueError('This event is not awaiting competition reconciliation')
                result = await bot.wom.request('GET', f'competitions/{competition_id}')
                if not matches(event, result, require_title=False):
                    raise ValueError('Competition group, metric or dates do not match this event')
                with bot.db.connection:
                    state = 'cancelled' if event['state'] == 'cancelled' else 'ready'
                    bot.db.connection.execute("UPDATE events SET competition_id=?,state=?,error=NULL WHERE id=?", (competition_id, state, event_id))
                bot.db.audit(interaction.user.id, 'event-reconcile', event_id, str(competition_id))
                return 'Existing competition attached; no new competition was created.'
        await reply(interaction, operation)
