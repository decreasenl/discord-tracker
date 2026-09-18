import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, UTC
from discord_tracker.integrations.wise_old_man import IntegrationError
from discord_tracker.services.event_operations import EventOperations

log = logging.getLogger(__name__)


class CompetitionWorker:
    def __init__(self, bot):
        self.bot = bot
        self.service = bot.competitions
        self.lock = asyncio.Lock()
        self.operations = EventOperations(bot)

    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if self.bot.is_ready():
                async with self.lock:
                    rows = self.service.conn.execute("SELECT id FROM events WHERE state NOT IN ('completed','cancelled','attention_required') ORDER BY id").fetchall()
                    for row in rows:
                        try:
                            await self.tick(row['id'])
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            detail = str(exc) if isinstance(exc, (ValueError, IntegrationError)) else f'Worker failed ({type(exc).__name__})'
                            if self.service.get(row['id'])['state'] == 'creating':
                                self.service.state(row['id'], 'reconciling', 'Creation response uncertain; checking for a matching competition')
                                self.bot.db.audit('scheduler', 'competition-create-uncertain', row['id'], type(exc).__name__)
                                log.warning('Event %s creation uncertain; reconciliation queued', row['id'])
                                continue
                            self.service.state(row['id'], 'attention_required', detail[:500])
                            self.bot.db.audit('scheduler', 'event-error', row['id'], type(exc).__name__)
                            log.error('Event %s requires attention (%s)', row['id'], type(exc).__name__)
                            self.bot.alerts.enqueue(f"Event #{row['id']}: {detail[:500]}. Use /events to inspect and /event-retry or /event-reconcile after review.")
                await self.bot.alerts.flush()
            await asyncio.sleep(15)

    async def message(self, event):
        channel = self.bot.get_channel(int(event['channel_id'])) or await self.bot.fetch_channel(int(event['channel_id']))
        return await channel.fetch_message(int(event['message_id']))

    async def tick(self, event_id):
        event = self.service.get(event_id)
        current = time.time()
        if event['state'] == 'reconciling' and current >= event['reconcile_after']:
            attempts = event['reconcile_attempts'] + 1
            with self.service.conn:
                self.service.conn.execute('UPDATE events SET reconcile_attempts=?,reconcile_after=? WHERE id=?',
                                          (attempts, current + 30 * attempts, event_id))
            try:
                found = await self.operations.reconcile(event_id)
                if found:
                    self.bot.alerts.enqueue(f'Event #{event_id}: recovered Wise Old Man competition #{found}. No duplicate was created.')
                    return
                detail = 'No unique matching competition found'
            except (ValueError, IntegrationError) as exc:
                detail = str(exc)
            self.bot.db.audit('scheduler', 'reconcile-attempt', event_id, detail[:500])
            if attempts >= 3:
                self.service.state(event_id, 'attention_required', detail[:500])
                self.bot.alerts.enqueue(f'Event #{event_id}: {detail[:500]}. Use /event-reconcile for an existing competition; /event-retry only after verifying no creation occurred.')
            return
        if event['state'] == 'scheduled' and current >= event['opens']:
            if current >= event['closes']:
                raise ValueError('Poll window was missed')
            options = self.service.select_options(event['kind'], current)
            with self.service.conn:
                self.service.conn.execute("UPDATE events SET options=?,state='posting' WHERE id=?", (json.dumps(options), event_id))
            channel = self.bot.get_channel(int(event['channel_id'])) or await self.bot.fetch_channel(int(event['channel_id']))
            text = (f"{event['kind'].title()} of the Week — poll #{event_id}\n" +
                    '\n'.join(f'{index}. {option}' for index, option in enumerate(options, 1)) +
                    f"\nVote using /vote event_id:{event_id} choice:NUMBER. You may change your vote.\nCloses <t:{int(event['closes'])}:F>.")
            message = await channel.send(text)
            with self.service.conn:
                self.service.conn.execute("UPDATE events SET message_id=?,state='voting' WHERE id=?", (str(message.id), event_id))
            self.bot.db.audit('scheduler', 'poll-posted', event_id, str(message.id))
            return
        if event['state'] == 'voting' and current >= event['closes']:
            await self.message(event)  # Missing poll requires intervention before selecting a winner.
            if current >= event['starts']:
                raise ValueError('Competition start was missed')
            self.service.choose(event_id)
            return
        if event['state'] == 'selected':
            if current >= event['starts']:
                raise ValueError('Competition start was missed')
            secret = os.getenv('WOM_GROUP_VERIFICATION_CODE', '')
            if not secret:
                raise ValueError('WOM_GROUP_VERIFICATION_CODE is required')
            title = event['request_title'] or f"{event['kind'].title()} of the Week [event {event_id}-{uuid.uuid4().hex[:12]}]"
            payload = {'title': title,
                       'metric': event['winner'], 'groupId': event['group_id'], 'groupVerificationCode': secret,
                       'startsAt': datetime.fromtimestamp(event['starts'], UTC).isoformat(),
                       'endsAt': datetime.fromtimestamp(event['ends'], UTC).isoformat()}
            with self.service.conn:
                self.service.conn.execute("UPDATE events SET state='creating',request_title=?,reconcile_attempts=0,reconcile_after=0 WHERE id=?", (title, event_id))
            result = await self.bot.wom.request('POST', 'competitions', payload)
            competition = result.get('competition', result)
            competition_id = competition.get('id')
            if not isinstance(competition_id, int):
                raise ValueError('Invalid competition response; reconcile manually')
            # Do not store the returned verification code in ordinary database records.
            with self.service.conn:
                self.service.conn.execute("UPDATE events SET competition_id=?,state='ready' WHERE id=?", (competition_id, event_id))
            self.bot.db.audit('scheduler', 'competition-created', event_id, str(competition_id))
            return
        if event['state'] in ('ready', 'waiting', 'active'):
            self.service.enqueue_next(event_id)
            if event['state'] == 'waiting' and current < event['starts']:
                return
            if event['state'] in ('waiting', 'active') and current < event['ends']:
                if event['state'] == 'waiting':
                    self.service.state(event_id, 'active')
                    self.bot.db.audit('scheduler', 'event-active', event_id, 'started')
                return
            message = await self.message(event)
            if event['state'] == 'ready':
                await message.edit(content=f"Poll #{event_id}: {event['winner']} selected.\nhttps://wiseoldman.net/competitions/{event['competition_id']}")
                self.service.state(event_id, 'active' if current >= event['starts'] else 'waiting')
            if current >= event['ends']:
                await message.edit(content=f"Event #{event_id} completed: {event['winner']}.\nResults: https://wiseoldman.net/competitions/{event['competition_id']}")
                self.service.complete(event_id)
                self.bot.db.audit('scheduler', 'event-completed', event_id, str(event['competition_id']))
