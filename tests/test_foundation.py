import asyncio
import sqlite3
import httpx
import pytest
from discord_tracker.bot.permissions import permitted
from discord_tracker.config import Config, bootstrap_settings
from discord_tracker.integrations.wise_old_man import WiseOldMan, IntegrationError
from discord_tracker.services.members import Members
from discord_tracker.storage.database import Database


SETTINGS = {'DISCORD_GUILD_ID': '1', 'DISCORD_BOT_ACCESS_ROLE_ID': '2', 'DISCORD_MANAGER_ROLE_ID': '3'}
PLAYER = {'id': 42, 'username': 'zezima', 'updatedAt': '2026-01-01T00:00:00Z'}


@pytest.mark.parametrize('guild,roles,manager,expected', [
    (1, [2], False, True), (1, [2], True, False), (1, [3], True, True),
    (1, [3], False, True), (2, [3], True, False), (None, [3], False, False),
    (1, [], False, False), (1, [999], True, False)])
def test_permissions(guild, roles, manager, expected):
    assert permitted(SETTINGS, guild, roles, manager) is expected


def test_persistence_identity_and_duplicate_links(tmp_path):
    path = tmp_path / 'test.sqlite3'
    db = Database(path)
    db.seed(SETTINGS)
    db.seed({**SETTINGS, 'DISCORD_MANAGER_ROLE_ID': '99'})
    db.link(3, 10, 'Old nickname', PLAYER)
    db.link(3, 10, 'New nickname', PLAYER)
    assert db.member(10)['display_name'] == 'New nickname'
    with pytest.raises(ValueError, match='already linked'):
        db.link(3, 11, 'Other', PLAYER)
    with pytest.raises(ValueError, match='already linked'):
        db.link(3, 10, 'Other', {**PLAYER, 'id': 43})
    db.close()
    db = Database(path)
    assert db.settings() == SETTINGS
    assert db.member(10)['player_id'] == 42
    assert db.connection.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == 2
    db.close()


def test_config_token_not_in_repr(monkeypatch):
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'secret-test-value')
    assert 'secret-test-value' not in repr(Config.load())
    monkeypatch.delenv('DISCORD_BOT_TOKEN')
    with pytest.raises(ValueError):
        Config.load()


def test_invalid_bootstrap(monkeypatch):
    monkeypatch.setenv('DISCORD_GUILD_ID', 'invalid')
    with pytest.raises(ValueError, match='DISCORD_GUILD_ID'):
        bootstrap_settings()


@pytest.mark.parametrize('status', [403, 404, 429, 500])
async def test_write_errors_are_sanitized_and_not_retried(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='secret-response-body')
    client = httpx.AsyncClient(base_url='https://example.test/', transport=httpx.MockTransport(handler))
    wom = WiseOldMan(client)
    with pytest.raises(IntegrationError) as error:
        await wom.player(username='zezima', refresh=True)
    assert 'secret-response-body' not in str(error.value)
    assert len(calls) == 1
    await wom.close()


async def test_invalid_player_response():
    client = httpx.AsyncClient(base_url='https://example.test/', transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    wom = WiseOldMan(client)
    with pytest.raises(IntegrationError):
        await wom.player(username='zezima')
    await wom.close()


async def test_refresh_coalesces_and_persists(tmp_path):
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()
    async def handler(request):
        calls.append(request.method)
        if request.method == 'POST':
            started.set()
            await release.wait()
        return httpx.Response(200, json=PLAYER)
    wom = WiseOldMan(httpx.AsyncClient(base_url='https://example.test/', transport=httpx.MockTransport(handler)))
    db = Database(tmp_path / 'test.sqlite3')
    db.link(3, 10, 'name', PLAYER)
    service = Members(db, wom)
    first = asyncio.create_task(service.retrieve(3, 10, refresh=True))
    await started.wait()
    second = asyncio.create_task(service.retrieve(3, 10, refresh=True))
    await asyncio.sleep(0)
    release.set()
    assert await first == await second == PLAYER
    assert calls == ['GET', 'POST']
    assert db.member(10)['snapshot']
    with pytest.raises(ValueError, match='60 seconds'):
        await service.retrieve(3, 10, refresh=True)
    await wom.close()
    db.close()


async def test_changed_identity_not_persisted(tmp_path):
    wom = WiseOldMan(httpx.AsyncClient(base_url='https://example.test/', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={**PLAYER, 'id': 99}))))
    db = Database(tmp_path / 'test.sqlite3')
    db.link(3, 10, 'name', PLAYER)
    service = Members(db, wom)
    with pytest.raises(IntegrationError, match='identity'):
        await service.retrieve(3, 10)
    assert db.member(10)['player_id'] == 42
    assert db.connection.execute('SELECT outcome FROM audit ORDER BY id DESC').fetchone()[0] == 'failed'
    db.close()
    await wom.close()


def test_reject_future_schema(tmp_path):
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError, match='newer'):
        Database(path)
