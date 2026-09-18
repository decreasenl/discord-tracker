import time
from datetime import datetime
import discord
from discord_tracker.storage.database import now


def register(bot, guild, reply):
    @bot.tree.command(name='rank', description='View your clan rank override', guild=guild)
    async def rank(interaction: discord.Interaction):
        if not await bot.authorize(interaction):
            return
        async def operation():
            member = bot.db.member(interaction.user.id)
            if not member or member['archived_at']:
                raise ValueError('An active linked membership is required')
            row = bot.db.connection.execute('SELECT * FROM rank_overrides WHERE discord_id=?', (str(interaction.user.id),)).fetchone()
            if not row or (row['expires_at'] is not None and row['expires_at'] <= time.time()):
                return 'No active manual rank. Automatic ranking is disabled pending agreed rules.'
            expiry = f"Expires <t:{int(row['expires_at'])}:F>." if row['expires_at'] else 'No expiry.'
            return f"Clan rank: {discord.utils.escape_markdown(row['rank'])}\nReason: {row['reason']}\n{expiry}"
        await reply(interaction, operation)

    @bot.tree.command(name='rank-set', description='Manager: assign a manual clan rank override', guild=guild)
    async def set_rank(interaction: discord.Interaction, member: discord.Member, rank: str, reason: str, expires: str | None = None):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            existing = bot.db.member(member.id)
            if not existing or existing['archived_at']:
                raise ValueError('An active linked membership is required')
            if not rank.strip() or len(rank) > 80 or not reason.strip() or len(reason) > 500:
                raise ValueError('Provide a rank up to 80 characters and a reason up to 500 characters')
            expiry = None
            if expires:
                try:
                    date = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                    if date.tzinfo is None or date.timestamp() <= time.time():
                        raise ValueError()
                    expiry = date.timestamp()
                except ValueError:
                    raise ValueError('Expiry must be a future ISO timestamp with timezone offset') from None
            with bot.db.connection:
                bot.db.connection.execute('''INSERT INTO rank_overrides VALUES(?,?,?,?,?) ON CONFLICT(discord_id)
                    DO UPDATE SET rank=excluded.rank,reason=excluded.reason,actor=excluded.actor,expires_at=excluded.expires_at''',
                    (str(member.id), rank.strip(), reason.strip(), str(interaction.user.id), expiry))
                bot.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                          (now(), str(interaction.user.id), 'rank-set', str(member.id), f'{rank}: {reason}; expiry={expiry}'))
            return 'Manual clan rank saved. Discord roles and in-game ranks are unchanged.'
        await reply(interaction, operation)

    @bot.tree.command(name='rank-clear', description='Manager: remove a manual clan rank override', guild=guild)
    async def clear_rank(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            if not reason.strip():
                raise ValueError('A reason is required')
            with bot.db.connection:
                bot.db.connection.execute('DELETE FROM rank_overrides WHERE discord_id=?', (str(member.id),))
                bot.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                          (now(), str(interaction.user.id), 'rank-clear', str(member.id), reason[:500]))
            return 'Override cleared. Automatic ranking remains disabled.'
        await reply(interaction, operation)
