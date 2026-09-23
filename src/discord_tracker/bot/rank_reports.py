"""Bounded private report pages; changes first, then deferred/held members."""
import math
import discord


def label(value, limit=30):
    text = ' '.join(str(value if value is not None else 'None').split())
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text[:limit]))


def report_page(report, page=1, *, title):
    results = report['results']
    changed = sum(r['previous'] != r['assigned'] for r in results)
    deferred = sum(r['reason'].startswith('Deferred:') for r in results)
    pages = max(1, math.ceil(len(results) / 2))
    if not 1 <= page <= pages:
        raise ValueError(f'Page must be between 1 and {pages}')
    ordered = sorted(results, key=lambda r: (r['previous'] == r['assigned'],
                                            not r['reason'].startswith('Deferred:'), r['discord_id']))
    lines = [title, f"Evaluated: {report['at']}",
             f"XP window: {report['period_start']} to {report['period_end']} (exclusive)",
             f"Rules: {report['rules_hash'][:12]}",
             f'{len(results)} members | {changed} changes | {deferred} deferred | {len(results) - changed - deferred} unchanged/held',
             f'Page {page}/{pages}. Application ranks only; Discord/in-game roles unchanged.']
    for r in ordered[(page - 1) * 2:page * 2]:
        xp = r['xp_gained'] if r['xp_gained'] is not None else 'unavailable'
        lines.append(f"\n{label(r['display_name'])} / OSRS: {label(r['username'])} (Discord {r['discord_id']})\n"
                     f"{label(r['previous'])} -> {label(r['assigned'])}; calculated: {label(r['calculated'])}\n"
                     f"Joined: {r['joined_on'] or 'unknown'}; XP gained: {xp}\n{label(r['reason'], 110)}")
    return '\n'.join(lines)
