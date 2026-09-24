from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord_tracker.services.member_sync import MemberSync, first_group_player, listed_names, normalized
from discord_tracker.storage.database import Database


PLAYERS = [
    {'id': 1, 'username': 'Su Do'},
    {'id': 2, 'username': 'Su'},
    {'id': 3, 'username': 'Segx'},
]


def member(member_id, name):
    return SimpleNamespace(id=member_id, display_name=name)


def test_first_name_matching_and_whitespace():
    assert normalized('  Su   Do ') == 'Su Do'
    for display in ('Su Do | Segx', 'su   do / Segx', 'Su Do, Segx', 'Su Do • Segx', 'Su Do  Segx', 'Su Do - Segx'):
        assert first_group_player(display, PLAYERS)['id'] == 1
    assert first_group_player('Segx | Su Do', PLAYERS)['id'] == 3
    assert first_group_player('Unrelated | Su Do', PLAYERS)['id'] == 1
    assert first_group_player('Just_Woolsey/Woolseyy', PLAYERS + [{'id': 4, 'username': 'Woolseyy'}])['id'] == 4
    assert first_group_player('Unknown | Segx / Su Do', PLAYERS)['id'] == 3
    assert listed_names(r'First\Second, Third • Fourth') == ['First', 'Second', 'Third', 'Fourth']
    # Whitespace remains part of an OSRS name rather than a delimiter.
    assert first_group_player('Su SomeoneElse', PLAYERS) is None


async def test_preview_apply_conflicts_and_idempotence(tmp_path):
    db = Database(tmp_path / 'members.sqlite3')
    wom = SimpleNamespace(group_members=AsyncMock(return_value=PLAYERS))
    service = MemberSync(db, wom)
    members = [member(10, 'Su Do | Segx'), member(11, 'Unknown'), member(12, 'Segx')]
    plan = await service.plan(members, 99)
    assert [item['status'] for item in plan] == ['proposed', 'unmatched', 'proposed']
    assert db.connection.execute('SELECT count(*) FROM members').fetchone()[0] == 0
    assert service.apply(7, plan) == 2
    assert db.member(10)['player_id'] == 1
    assert db.member(12)['player_id'] == 3
    repeat = await service.plan(members, 99)
    assert [item['status'] for item in repeat] == ['already', 'unmatched', 'already']
    assert service.apply(7, repeat) == 0
    db.close()


async def test_duplicate_discord_claims_are_not_applied(tmp_path):
    db = Database(tmp_path / 'members.sqlite3')
    service = MemberSync(db, SimpleNamespace(group_members=AsyncMock(return_value=PLAYERS)))
    plan = await service.plan([member(10, 'Su Do | One'), member(11, 'Su Do / Two')], 99)
    assert all(item['status'] == 'conflict' for item in plan)
    assert service.apply(7, plan) == 0
    db.close()
