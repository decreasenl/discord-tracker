"""Preview-first Discord profile to WOM member matching."""
import re


def normalized(value):
    return ' '.join(value.split())


def listed_names(display_name):
    # Underscores and unspaced hyphens may be part of a RuneScape-style name,
    # so only split on unambiguous separators. Whitespace inside each candidate
    # is normalized afterwards.
    parts = re.split(r'(?:[|/,;\\•·]+|\s+[\-–—]\s+)', display_name.strip())
    candidates = []
    for part in parts:
        whole = normalized(part)
        if not whole:
            continue
        candidates.append(whole)
        # Prefer the complete normalized value, allowing repeated whitespace
        # inside an OSRS name. If that is not a WOM member, these alternatives
        # also support repeated whitespace being used as a delimiter.
        if re.search(r'\s{2,}', part):
            candidates.extend(normalized(piece) for piece in re.split(r'\s{2,}', part) if normalized(piece))
    return candidates


def first_group_player(display_name, players):
    """Return the first listed profile name that exactly matches the WOM group."""
    by_name = {}
    for player in players:
        by_name.setdefault(normalized(player['username']).casefold(), []).append(player)
    for candidate in listed_names(display_name):
        matches = by_name.get(candidate.casefold(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


class MemberSync:
    def __init__(self, db, wom):
        self.db, self.wom = db, wom

    async def plan(self, discord_members, group_id):
        players = await self.wom.group_members(group_id)
        results = []
        for member in discord_members:
            player = first_group_player(member.display_name, players)
            item = {'discord_id': str(member.id), 'display_name': member.display_name,
                    'player': player, 'status': 'unmatched', 'detail': 'No listed profile name exactly matches a WOM group username'}
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
