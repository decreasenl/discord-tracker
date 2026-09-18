"""Persistent manager notifications with bounded backoff and log fallback."""
import logging
import time
import discord
from discord_tracker.storage.database import now

log = logging.getLogger(__name__)


class Alerts:
    def __init__(self, bot):
        self.bot = bot

    def enqueue(self, message):
        # Callers supply sanitized summaries, never exception payloads or secrets.
        with self.bot.db.connection:
            self.bot.db.connection.execute('INSERT INTO alerts(created_at,message) VALUES(?,?)', (now(), message[:1800]))
        log.warning('%s', message[:1800])

    async def flush(self):
        channel_id = self.bot.db.settings().get('ALERT_CHANNEL_ID')
        if not channel_id:
            return  # Remain queued until a manager configures the destination.
        rows = self.bot.db.connection.execute('''SELECT * FROM alerts WHERE delivered_at IS NULL
            AND next_attempt<=? ORDER BY id LIMIT 3''', (time.time(),)).fetchall()
        for row in rows:
            try:
                channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
                if str(channel.guild.id) != self.bot.db.settings().get('DISCORD_GUILD_ID'):
                    raise ValueError('Alert channel belongs to another server')
                await channel.send(f"[Alert #{row['id']}] {row['message']}", allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                delay = min(3600, 30 * 2 ** min(row['attempts'], 7))
                with self.bot.db.connection:
                    self.bot.db.connection.execute('UPDATE alerts SET attempts=attempts+1,next_attempt=? WHERE id=?',
                                                   (time.time()+delay, row['id']))
                log.error('Alert %s delivery failed (%s); retained for retry', row['id'], type(exc).__name__)
            else:
                with self.bot.db.connection:
                    self.bot.db.connection.execute('UPDATE alerts SET delivered_at=? WHERE id=?', (now(), row['id']))
                self.bot.db.audit('notifier', 'alert-delivered', row['id'], str(channel_id))
