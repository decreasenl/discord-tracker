import asyncio
import logging
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class RankWorker:
    def __init__(self, bot):
        self.bot = bot
        self.retry_after = None

    async def tick(self, instant=None):
        instant = instant or datetime.now(UTC)
        rules = self.bot.ranks.rules
        settings = self.bot.db.settings()
        if not rules.enabled or (self.retry_after and instant < self.retry_after):
            return
        timezone = settings.get('COMMUNITY_TIMEZONE', 'Europe/Amsterdam')
        local = instant.astimezone(ZoneInfo(timezone))
        key = f'{local.date()}:{timezone}:{rules.digest}'
        if local.time() < rules.daily_at or settings.get('RANK_LAST_RUN') == key:
            return
        try:
            results = await self.bot.ranks.evaluate(instant=instant)
            changed = sum(r['assigned'] != r['previous'] for r in results)
            deferred = sum(r['reason'].startswith('Deferred:') for r in results)
            self.bot.db.setting('scheduler', 'RANK_LAST_RUN', key)
            log.info('Rank evaluation completed: members=%s changed=%s deferred=%s', len(results), changed, deferred)
            if changed or deferred:
                self.bot.alerts.enqueue(f'Rank evaluation: {changed} application rank change(s), {deferred} member(s) deferred. Use /rank-summary for saved per-member results, or /rank-summary last_upgrade:true for the last run with changes. Discord and in-game roles unchanged.')
        except Exception as exc:
            self.retry_after = instant + timedelta(hours=1)
            log.error('Rank evaluation failed (%s)', type(exc).__name__)
            self.bot.db.audit('scheduler', 'rank-evaluation', 'all', type(exc).__name__)
            self.bot.alerts.enqueue('Automatic rank evaluation failed. Check /set-group, config/ranks.yaml and Wise Old Man /diagnostics; use /rank-preview to inspect an individual member. Retrying in one hour.')

    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if self.bot.is_ready():
                await self.tick()
            await asyncio.sleep(30)
