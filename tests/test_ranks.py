from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from discord_tracker.integrations.wise_old_man import WiseOldMan
from discord_tracker.jobs.ranks import RankWorker
from discord_tracker.rank_rules import RankRules, previous_month
from discord_tracker.services.ranks import Ranks, observed_xp
from discord_tracker.storage.database import Database


INSTANT = datetime(2026, 9, 22, 5, tzinfo=UTC)


def observation(gained=10):
    return {'player': {'id': 7}, 'startDate': '2026-08-01T01:00:00Z',
            'endDate': '2026-08-30T23:00:00Z',
            'data': [{'metric': 'overall', 'start': 100, 'end': 100 + gained, 'gained': gained}]}


@pytest.fixture
def ranks(tmp_path):
    db = Database(tmp_path / 'ranks.sqlite3')
    db.seed({'WOM_GROUP_ID': '42', 'COMMUNITY_TIMEZONE': 'Europe/Amsterdam'})
    db.link(1, 2, 'Member', {'id': 7, 'username': 'player'})
    service = Ranks(db, SimpleNamespace(group_gains=AsyncMock(return_value={7: observation()})), RankRules.load())
    service.joined(1, 2, '2026-06-22', 'Actual joining date', 'Europe/Amsterdam')
    yield service
    db.close()


def test_calendar_milestones_and_dst():
    rules = RankRules.load()
    for name, months in rules.ranks:
        year, month = divmod(2024 * 12 + months, 12)
        assert rules.calculated(date(2024, 1, 15), date(year, month + 1, 15)) == name
    assert rules.calculated(date(2024, 1, 31), date(2024, 2, 28)) is None
    assert rules.calculated(date(2024, 1, 31), date(2024, 2, 29)) == 'Squire'
    start, end = previous_month(datetime(2026, 4, 10, tzinfo=UTC), 'Europe/Amsterdam')
    assert start.isoformat() == '2026-03-01T00:00:00+01:00'
    assert end.isoformat() == '2026-04-01T00:00:00+02:00'


def test_policy_rejects_invalid_config(tmp_path):
    import yaml
    import json
    config = json.loads(RankRules.load().document)
    config['ranks'][0]['name'] = 'Captain'
    path = tmp_path / 'rules.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid config/ranks.yaml'):
        RankRules.load(path)
    path.write_text('enabled: true\nenabled: false\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid config/ranks.yaml'):
        RankRules.load(path)


async def test_preview_promote_and_repeat(ranks):
    preview = (await ranks.evaluate(1, 2, preview=True, instant=INSTANT))[0]
    assert preview['assigned'] == 'Striker'
    assert ranks.state(2)['assigned'] is None
    assert ranks.db.connection.execute('SELECT count(*) FROM rank_evaluations').fetchone()[0] == 0
    await ranks.evaluate(instant=INSTANT)
    await ranks.evaluate(instant=INSTANT)
    await ranks.evaluate(instant=INSTANT)
    assert ranks.state(2)['assigned'] == 'Striker'
    assert ranks.db.connection.execute("SELECT count(*) FROM audit WHERE action='rank-automatic'").fetchone()[0] == 1


async def test_no_activity_missing_data_and_missing_join_date(ranks):
    for value, reason in [(0, 'Unchanged:'), (-1, 'Deferred:'), (None, 'Deferred:')]:
        ranks.wom.group_gains.return_value = {7: observation(value)} if value is not None else {}
        result = (await ranks.evaluate(instant=INSTANT))[0]
        assert result['assigned'] is None
        assert result['reason'].startswith(reason)
    with ranks.db.connection:
        ranks.db.connection.execute('UPDATE members SET joined_on=NULL')
    ranks.wom.group_gains.return_value = {7: observation()}
    assert 'joining date' in (await ranks.evaluate(instant=INSTANT))[0]['reason']


async def test_overrides_expiry_staff_and_no_demotions(ranks):
    future = datetime(2099, 1, 1, tzinfo=UTC).timestamp()
    ranks.set_override(1, 2, 'Squire', 'Manual hold', future)
    result = (await ranks.evaluate(instant=INSTANT))[0]
    assert result['assigned'] == 'Squire' and result['calculated'] == 'Striker'
    with ranks.db.connection:
        ranks.db.connection.execute('UPDATE rank_overrides SET expires_at=1')
    ranks.wom.group_gains.return_value = {7: observation(0)}
    assert (await ranks.evaluate(instant=INSTANT))[0]['assigned'] == 'Squire'
    ranks.clear_override(1, 2, 'Resume automation')
    assert ranks.state(2)['assigned'] == 'Squire'
    ranks.wom.group_gains.return_value = {7: observation()}
    assert (await ranks.evaluate(instant=INSTANT))[0]['assigned'] == 'Striker'
    # Repeating an earlier decision after a genuine manager intervention is allowed.
    ranks.set_override(1, 2, 'Squire', 'Reset')
    ranks.clear_override(1, 2, 'Resume')
    assert (await ranks.evaluate(instant=INSTANT))[0]['assigned'] == 'Striker'
    for name in ('Captain', 'Paladin'):
        ranks.set_override(1, 2, name, 'Manual appointment')
        ranks.clear_override(1, 2, 'Clear hold')
        assert (await ranks.evaluate(instant=INSTANT))[0]['assigned'] == name


def test_bad_xp_evidence_is_not_activity():
    start, end = previous_month(INSTANT, 'Europe/Amsterdam')
    for field, value in [('startDate', None), ('endDate', '2026-09-01T00:00:00Z')]:
        row = observation()
        row[field] = value
        assert observed_xp(row, start, end) is None
    row = observation()
    row['data'][0]['start'] = -1
    assert observed_xp(row, start, end) is None


async def test_worker_restart_and_failure_retry(ranks):
    bot = SimpleNamespace(ranks=ranks, db=ranks.db, alerts=SimpleNamespace(enqueue=Mock()))
    worker = RankWorker(bot)
    await worker.tick(INSTANT)
    await RankWorker(bot).tick(INSTANT)
    assert ranks.wom.group_gains.await_count == 1
    assert ranks.db.settings()['RANK_LAST_RUN'].startswith('2026-09-22')
    ranks.wom.group_gains.side_effect = RuntimeError('network unavailable')
    next_day = datetime(2026, 9, 23, 5, tzinfo=UTC)
    await worker.tick(next_day)
    await worker.tick(next_day)
    assert ranks.wom.group_gains.await_count == 2
    assert ranks.db.settings()['RANK_LAST_RUN'].startswith('2026-09-22')
    assert 'failed' in bot.alerts.enqueue.call_args.args[0]


async def test_group_gains_adapter():
    def handler(request):
        assert request.url.path == '/v2/groups/42/bulk-gained'
        assert request.url.params['startDate'] == '2026-07-31T22:00:00+00:00'
        return httpx.Response(200, json=[observation()])
    client = WiseOldMan(httpx.AsyncClient(base_url='https://example.test/v2/', transport=httpx.MockTransport(handler)))
    assert (await client.group_gains(42, *previous_month(INSTANT, 'Europe/Amsterdam')))[7]['data'][0]['gained'] == 10
    await client.close()


async def test_disabled_preview_and_archival_during_fetch(ranks):
    from dataclasses import replace
    ranks.rules = replace(ranks.rules, enabled=False)
    assert (await ranks.evaluate(instant=INSTANT))[0]['assigned'] is None
    ranks.wom.group_gains.assert_not_awaited()
    assert (await ranks.evaluate(preview=True, instant=INSTANT))[0]['assigned'] == 'Striker'
    assert ranks.state(2)['assigned'] is None
    ranks.rules = replace(ranks.rules, enabled=True)

    async def archive(*args):
        ranks.db.change_member(1, 2, 'archive', 'Left during fetch')
        return {7: observation()}

    ranks.wom.group_gains.side_effect = archive
    assert await ranks.evaluate(instant=INSTANT) == []
    assert ranks.state(2)['assigned'] is None


def test_upgrade_preserves_expired_manual_rank(tmp_path):
    path = tmp_path / 'old.sqlite3'
    db = Database(path)
    db.link(1, 2, 'Member', {'id': 7, 'username': 'player'})
    # Construct the immediately preceding schema on this disposable test database.
    with db.connection:
        db.connection.execute("INSERT INTO rank_overrides VALUES('2','Captain','Staff','1',1)")
        db.connection.execute('DROP TABLE rank_evaluations')
        db.connection.execute('DROP TABLE member_ranks')
        db.connection.execute('ALTER TABLE members DROP COLUMN joined_on')
        db.connection.execute('PRAGMA user_version=5')
    db.close()
    db = Database(path)
    assert db.member(2)['joined_on'] is None
    assert db.connection.execute('SELECT assigned FROM member_ranks').fetchone()[0] == 'Captain'
    db.close()
