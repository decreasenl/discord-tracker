import time
from datetime import datetime

import discord


def describe(result):
    return (f"Assigned: {result['previous'] or 'None'} -> {result['assigned'] or 'None'}\n"
            f"Calculated by membership: {result['calculated'] or 'None'}\n"
            f"Clan joined: {result['joined_on'] or 'unknown'}\n"
            f"Activity window: {result['period_start']} to {result['period_end']} (exclusive)\n"
            f"Recorded XP gained: {result['xp_gained'] if result['xp_gained'] is not None else 'unavailable'}\n"
            f"Decision: {result['reason']}\nRules: {result['rules_hash'][:12]}")


def register(bot, guild, reply):
    @bot.tree.command(name='rank', description='View your assigned and calculated application clan rank', guild=guild)
    async def rank(interaction: discord.Interaction):
        if not await bot.authorize(interaction):
            return
        async def operation():
            member = bot.ranks.active_member(interaction.user.id)
            state = bot.ranks.state(interaction.user.id)
            override = bot.ranks.override(interaction.user.id)
            active = override and (override['expires_at'] is None or override['expires_at'] > time.time())
            override_text = f"Active manual override: {override['reason']}" if active else 'No active manual override.'
            return discord.utils.escape_markdown(
                f"Application clan rank: {state['assigned'] or 'None'}\n"
                f"Last calculated rank: {state['calculated'] or 'Not yet evaluated'}\n"
                f"Clan joined: {member['joined_on'] or 'unknown'}\n{override_text}\n"
                f"Last evaluation: {state['evaluated_at'] or 'Never'}\n"
                f"{state['explanation'] or 'Awaiting evaluation.'}\nDiscord and in-game roles are unchanged.")
        await reply(interaction, operation)

    @bot.tree.command(name='member-joined', description='Manager: record the actual clan joining date', guild=guild)
    async def member_joined(interaction: discord.Interaction, member: discord.Member, joined_on: str, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            bot.ranks.joined(interaction.user.id, member.id, joined_on, reason,
                             bot.settings.get('COMMUNITY_TIMEZONE', 'Europe/Amsterdam'))
            return 'Clan joining date saved. Use /rank-preview to inspect eligibility; ranks have not changed.'
        await reply(interaction, operation)

    @bot.tree.command(name='rank-preview', description='Manager: preview a member rank decision without applying it', guild=guild)
    async def rank_preview(interaction: discord.Interaction, member: discord.Member):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            result = (await bot.ranks.evaluate(interaction.user.id, member.id, preview=True))[0]
            return 'Preview as if automation enabled; no rank changes.\n' + discord.utils.escape_markdown(describe(result))
        await reply(interaction, operation)

    @bot.tree.command(name='rank-evaluate', description='Manager: evaluate ranks now for one member or all active members', guild=guild)
    async def rank_evaluate(interaction: discord.Interaction, member: discord.Member | None = None):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            results = await bot.ranks.evaluate(interaction.user.id, member.id if member else None)
            if member:
                return discord.utils.escape_markdown(describe(results[0])) + '\nDiscord and in-game roles unchanged.'
            changed = sum(r['previous'] != r['assigned'] for r in results)
            deferred = sum(r['reason'].startswith('Deferred:') for r in results)
            return f'Evaluated {len(results)} members: {changed} application rank changes, {deferred} deferred. Inspect /rank-preview and /audit. Discord and in-game roles unchanged.'
        await reply(interaction, operation)

    @bot.tree.command(name='rank-rules', description='Manager: view loaded automatic rank rules and schedule', guild=guild)
    async def rank_rules(interaction: discord.Interaction):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            rules = bot.ranks.rules
            return (f"Source: config/ranks.yaml; enabled={rules.enabled}; hash={rules.digest[:12]}\n"
                    f"Daily at {rules.daily_at:%H:%M} ({bot.settings.get('COMMUNITY_TIMEZONE', 'Europe/Amsterdam')}).\n"
                    f"Previous calendar month: at least {rules.minimum_gain} overall XP gained.\n"
                    + '\n'.join(f'{name}: {months} calendar month(s)' for name, months in rules.ranks)
                    + '\nStaff ranks protected; no automatic demotions. Restart after editing the rules file.')
        await reply(interaction, operation)

    @bot.tree.command(name='rank-set', description='Manager: assign a manual clan rank override', guild=guild)
    async def set_rank(interaction: discord.Interaction, member: discord.Member, rank: str, reason: str, expires: str | None = None):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            expiry = None
            if expires:
                try:
                    date = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                    if date.tzinfo is None or date.timestamp() <= time.time():
                        raise ValueError()
                    expiry = date.timestamp()
                except ValueError:
                    raise ValueError('Expiry must be a future ISO timestamp with timezone offset') from None
            bot.ranks.set_override(interaction.user.id, member.id, rank, reason, expiry)
            return 'Manual clan rank saved. Discord roles and in-game ranks are unchanged.'
        await reply(interaction, operation)

    @bot.tree.command(name='rank-clear', description='Manager: remove an override while retaining the assigned rank', guild=guild)
    async def clear_rank(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not await bot.authorize(interaction, True):
            return
        async def operation():
            bot.ranks.clear_override(interaction.user.id, member.id, reason)
            return 'Override cleared; assigned rank retained until an eligible promotion. Staff/custom ranks remain protected; use /rank-set to explicitly replace them.'
        await reply(interaction, operation)
