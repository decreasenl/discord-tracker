"""Preview-first Discord profile to WOM member matching."""
import re


def normalized(value):
    return ' '.join(value.split())


def first_group_player(display_name, players):
    """Find the longest WOM username at the beginning of a server display name.

    Whitespace inside an OSRS name is flexible. A following name must be
    separated by punctuation or at least two whitespace characters.
    """
    display = display_name.strip()
    matches = []
    for player in players:
        username = normalized(player['username'])
        pattern = r'^\s*' + r'\s+'.join(re.escape(part) for part in username.split()) + r'(?P<rest>.*)$'
        found = re.match(pattern, display, flags=re.IGNORECASE)
        if not found:
            continue
        rest = found.group('rest')
        if not rest.strip():
            matches.append((len(username), player))
            continue
        leading = len(rest) - len(rest.lstrip())
        first = rest.lstrip()[0]
        if leading >= 2 or not first.isalnum():
            matches.append((len(username), player))
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    winners = [player for length, player in matches if length == longest]
    return winners[0] if len(winners) == 1 else None


class MemberSync:
    def __init__(self, db, wom):
        self.db, self.wom = db, wom

    async def plan(self, discord_members, group_id):
        players = await self.wom.group_members(group_id)
        results = []
        for member in discord_members:
            player = first_group_player(member.display_name, players)
            item = {'discord_id': str(member.id), 'display_name': member.display_name,
                    'player': player, 'status': 'unmatched', 'detail': 'No WOM group username matches the first profile name'}
            if player:
                existing = self.db.member(member.id)
                owner = self.db.connection.execute('SELECT discord_id,archived_at FROM members WHERE player_id=?',
                                                   (player['id'],)).fetchone()
                if existing and existing['archived_at']:
                    item.update(status='conflict', detail='Discord member link is archived; restore or relink manually')
                elif existing and existing['player_id'] == player['id']:
                    item.update(status='already', detail='Existing link matches')
                elif existing:
                    item.update(status='conflict', detail=f"Already linked to {existing['username']}; use /relink manually")
                elif owner:
                    item.update(status='conflict', detail=f"OSRS account already belongs to Discord {owner['discord_id']}")
                else:
                    item.update(status='proposed', detail='Exact first-name match in configured WOM group')
            results.append(item)

        claims = {}
        for item in results:
            if item['status'] == 'proposed':
                claims.setdefault(item['player']['id'], []).append(item)
        for claimed in claims.values():
            if len(claimed) > 1:
                for item in claimed:
                    item.update(status='conflict', detail='Multiple Discord members claim the same first OSRS name')
        return results

    def apply(self, actor, results):
        applied = 0
        for item in results:
            if item['status'] != 'proposed':
                continue
            self.db.link(actor, item['discord_id'], item['display_name'], item['player'])
            item.update(status='linked', detail='Link created')
            applied += 1
        return applied
