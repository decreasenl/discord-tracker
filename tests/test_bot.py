from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
import discord
from discord_tracker.bot.client import Tracker
from discord_tracker.config import Config
from discord_tracker.storage.database import Database


async def test_register_command_contract(tmp_path):
    db = Database(tmp_path / 'test.sqlite3')
    bot = Tracker(Config('test-token', Path(tmp_path / 'test.sqlite3')), db)
    guild = discord.Object(id=1)
    bot.register_commands(guild)
    assert {c.name for c in bot.tree.get_commands(guild=guild)} == {
        'status', 'stats', 'member', 'link', 'refresh', 'diagnostics',
        'archive', 'restore', 'relink', 'settings', 'set-role', 'set-timezone', 'set-group', 'audit',
        'event-config', 'event-schedule', 'vote', 'competition', 'events', 'event-cancel', 'event-winner', 'event-reconcile', 'event-retry',
        'rank', 'rank-set', 'rank-clear', 'rank-preview', 'rank-evaluate', 'rank-rules', 'member-joined', 'rank-dry-run', 'rank-summary',
        'member-sync', 'set-alert-channel', 'test-alert'}
    for command in bot.tree.get_commands(guild=guild):
        assert command.to_dict(bot.tree)['name'] == command.name
    await bot.wom.close()
    db.close()


async def test_invalid_role_does_not_seed_database(tmp_path, monkeypatch):
    for name, value in [('DISCORD_GUILD_ID', '1'), ('DISCORD_BOT_ACCESS_ROLE_ID', '2'), ('DISCORD_MANAGER_ROLE_ID', '3')]:
        monkeypatch.setenv(name, value)
    db = Database(tmp_path / 'test.sqlite3')
    bot = Tracker(Config('test-token', tmp_path / 'test.sqlite3'), db)
    guild = MagicMock()
    guild.fetch_roles = AsyncMock(return_value=[])
    bot.fetch_guild = AsyncMock(return_value=guild)
    with pytest.raises(ValueError, match='assignable role'):
        await bot.setup_hook()
    assert db.settings() == {}
    assert not bot.ready_for_commands
    await bot.wom.close()
    db.close()


async def test_authorize_denies_dm_before_deferring(tmp_path):
    db = Database(tmp_path / 'test.sqlite3')
    bot = Tracker(Config('test-token', tmp_path / 'test.sqlite3'), db)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    assert not await bot.authorize(interaction)
    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    await bot.wom.close()
    db.close()
