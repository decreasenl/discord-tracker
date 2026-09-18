import asyncio
import time
from discord_tracker.integrations.wise_old_man import IntegrationError


class Members:
    def __init__(self, db, wom):
        self.db, self.wom = db, wom
        self.pending = {}
        self.last_refresh = {}

    async def link(self, actor, target, display_name, username):
        try:
            player = await self.wom.player(username=username)
            self.db.link(actor, target, display_name, player)
            return player
        except (IntegrationError, ValueError):
            self.db.audit(actor, 'link', target, 'failed')
            raise

    async def retrieve(self, actor, target, refresh=False):
        member = self.db.member(target)
        if member is None:
            raise ValueError("No linked OSRS account. Ask a manager to use /link first")
        if member['archived_at']:
            raise ValueError('Member is archived; a manager must restore them first')
        if refresh:
            key = member["player_id"]
            if key in self.pending:
                return await asyncio.shield(self.pending[key])
            if time.monotonic() - self.last_refresh.get(key, float('-inf')) < 60:
                raise ValueError("Please wait 60 seconds between refresh requests for this player")
            self.last_refresh[key] = time.monotonic()
            task = asyncio.create_task(self._fetch(actor, target, member, True))
            self.pending[key] = task
            def finished(completed):
                self.pending.pop(key, None)
                if not completed.cancelled():
                    completed.exception()  # Observe failure even if the caller disconnected.
            task.add_done_callback(finished)
            return await asyncio.shield(task)
        return await self._fetch(actor, target, member, False)

    async def relink(self, actor, target, username, reason):
        if not reason.strip():
            raise ValueError('A reason is required')
        player = await self.wom.player(username=username)
        self.db.change_member(actor, target, 'relink', reason, player)
        return player

    async def _fetch(self, actor, target, member, refresh):
        action = "refresh" if refresh else "retrieve"
        self.db.audit(actor, action, target, "pending")
        try:
            player = await self.wom.player(player_id=member["player_id"])
            if refresh:
                player = await self.wom.player(username=player["username"], refresh=True)
            if player["id"] != member["player_id"]:
                raise IntegrationError("Player identity changed; manager review required")
            self.db.snapshot(target, player)
            self.db.audit(actor, action, target, "completed")
            return player
        except asyncio.CancelledError:
            self.db.audit(actor, action, target, 'interrupted')
            raise
        except (IntegrationError, ValueError):
            self.db.audit(actor, action, target, "failed")
            raise
