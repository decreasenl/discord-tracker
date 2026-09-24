import math
import discord

from discord_tracker.bot.permissions import permitted


def safe(value, limit=48):
    value = ' '.join(str(value).split())[:limit]
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def report(results, page, applied):
    page_size = 8
    pages = max(1, math.ceil(len(results) / page_size))
    if not 1 <= page <= pages:
        raise ValueError(f'Page must be between 1 and {pages}')
    counts = {status: sum(r['status'] == status for r in results)
              for status in ('proposed', 'linked', 'already', 'unmatched', 'conflict')}
    heading = ('APPLIED' if applied else 'DRY RUN') + (
        f": {len(results)} eligible Discord members | {counts['linked']} linked | "
        f"{counts['proposed']} proposed | {counts['already']} already linked | "
        f"{counts['unmatched']} unmatched | {counts['conflict']} conflicts | page {page}/{pages}")
    lines = [heading]
    for item in results[(page - 1) * page_size:page * page_size]:
        username = item['player']['username'] if item['player'] else '—'
        lines.append(f"{safe(item['display_name'])} → {safe(username)} [{item['status']}]: {safe(item['detail'], 90)}")
    if not applied:
        lines.append('Nothing changed. Use apply:true after reviewing every page.')
    return '\n'.join(lines)


def register(bot, guild, reply):
    @bot.tree.command(name='member-sync', description='Manager: preview or apply Discord profile links from the WOM group', guild=guild)
    async def member_sync(interaction: discord.Interaction, apply: bool = False, page: int = 1):
        if not await bot.authorize(interaction, True):
            return

        async def operation():
            group_id = bot.db.settings().get('WOM_GROUP_ID')
            if not group_id:
                raise ValueError('Configure the Wise Old Man group with /set-group first')
            candidates = []
            async for member in interaction.guild.fetch_members(limit=None):
                if member.bot:
                    continue
                roles = [role.id for role in member.roles]
                if permitted(bot.settings, interaction.guild_id, roles):
                    candidates.append(member)
            results = await bot.member_sync.plan(candidates, int(group_id))
            applied = bot.member_sync.apply(interaction.user.id, results) if apply else 0
            bot.db.audit(interaction.user.id, 'member-sync', interaction.guild_id,
                         f"apply={apply}; eligible={len(results)}; linked={applied}; conflicts={sum(r['status'] == 'conflict' for r in results)}")
            return report(results, page, apply)

        await reply(interaction, operation)
