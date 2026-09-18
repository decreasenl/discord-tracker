import time
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from discord_tracker.storage.database import Database
from discord_tracker.services.competitions import Competitions
from discord_tracker.services.alerts import Alerts
from discord_tracker.services.event_operations import EventOperations
from discord_tracker.integrations.wise_old_man import WiseOldMan, IntegrationError


@pytest.fixture
def system(tmp_path):
    db = Database(tmp_path / 'ops.sqlite3')
    db.seed({'WOM_GROUP_ID': '1', 'DISCORD_GUILD_ID': '9', 'ALERT_CHANNEL_ID': '10'})
    competitions = Competitions(db)
    competitions.configure(1, 'skill', 'attack,defence', 2, 0)
    start = time.time() + 100
    event_id = competitions.schedule(1, 'skill', 10, start, start+100, start+200, start+300)
    with db.connection:
        db.connection.execute("UPDATE events SET winner='attack',request_title='unique-title',state='reconciling' WHERE id=?", (event_id,))
    bot = SimpleNamespace(db=db, competitions=competitions, wom=SimpleNamespace(request=AsyncMock(), group_competitions=AsyncMock()))
    bot.alerts = Alerts(bot)
    result = {'id': 55, 'title': 'unique-title', 'groupId': 1, 'metric': 'attack',
              'startsAt': datetime.fromtimestamp(start+200, UTC).isoformat(),
              'endsAt': datetime.fromtimestamp(start+300, UTC).isoformat()}
    yield bot, event_id, result
    db.close()


async def test_reconciliation_requires_unique_complete_match(system):
    bot, event_id, result = system
    ops = EventOperations(bot)
    bot.wom.group_competitions.return_value = [result, {**result, 'id': 56}]
    with pytest.raises(ValueError, match='Multiple'):
        await ops.reconcile(event_id)
    assert bot.competitions.get(event_id)['competition_id'] is None
    bot.wom.group_competitions.return_value = [{**result, 'metric': 'defence'}]
    assert await ops.reconcile(event_id) is None
    bot.wom.group_competitions.return_value = [result]
    bot.wom.request.return_value = result
    assert await ops.reconcile(event_id) == 55
    assert bot.competitions.get(event_id)['state'] == 'ready'
    assert all(call.args[0] == 'GET' for call in bot.wom.request.call_args_list)


async def test_delete_confirmation_failure_and_idempotent_recovery(system, monkeypatch):
    bot, event_id, result = system
    with bot.db.connection:
        bot.db.connection.execute("UPDATE events SET competition_id=55,state='active' WHERE id=?", (event_id,))
    ops = EventOperations(bot)
    monkeypatch.setenv('WOM_GROUP_VERIFICATION_CODE', 'private-code')
    with pytest.raises(ValueError, match='Confirm'):
        await ops.cancel(event_id, 1, 'cancel test', True, 99)
    assert bot.competitions.get(event_id)['state'] == 'active'
    bot.wom.request.assert_not_awaited()
    bot.wom.request.side_effect = [result, IntegrationError('Connection failed')]
    response = await ops.cancel(event_id, 1, 'cancel test', True, 55)
    assert 'PARTIAL:' in response
    assert bot.competitions.get(event_id)['state'] == 'cancelled'
    assert bot.competitions.get(event_id)['deletion_state'] == 'unknown'
    assert bot.db.connection.execute('SELECT count(*) FROM alerts').fetchone()[0] == 1
    bot.wom.request.reset_mock()
    bot.wom.request.side_effect = IntegrationError('Not found', 404)
    assert 'already absent' in await ops.cancel(event_id, 1, 'retry cleanup', True, 55)
    assert bot.competitions.get(event_id)['deletion_state'] == 'deleted'
    assert bot.wom.request.call_args.args[0] == 'GET'


async def test_alert_retry_survives_failed_delivery(system):
    bot, _, _ = system
    channel = SimpleNamespace(guild=SimpleNamespace(id=9), send=AsyncMock(side_effect=RuntimeError('offline')))
    bot.get_channel = lambda _: channel
    bot.alerts.enqueue('A safe actionable message')
    await bot.alerts.flush()
    row = bot.db.connection.execute('SELECT * FROM alerts').fetchone()
    assert row['delivered_at'] is None and row['attempts'] == 1
    await bot.alerts.flush()
    assert channel.send.await_count == 1  # Backoff prevents a tight retry loop.
    with bot.db.connection:
        bot.db.connection.execute('UPDATE alerts SET next_attempt=0')
    channel.send.side_effect = None
    await Alerts(bot).flush()
    assert bot.db.connection.execute('SELECT delivered_at FROM alerts').fetchone()[0]


async def test_group_pagination_and_empty_delete_response():
    offsets = []
    def handler(request):
        if request.method == 'DELETE':
            return httpx.Response(204)
        offset = int(request.url.params['offset'])
        offsets.append(offset)
        return httpx.Response(200, json=[{'id': i} for i in range(offset, offset+20)] if offset == 0 else [{'id': 20}])
    wom = WiseOldMan(httpx.AsyncClient(base_url='https://example.test/', transport=httpx.MockTransport(handler)))
    assert len(await wom.group_competitions(1)) == 21
    assert offsets == [0, 20]
    assert await wom.request('DELETE', 'competitions/1', {'verificationCode': 'test'}) == {}
    await wom.close()
