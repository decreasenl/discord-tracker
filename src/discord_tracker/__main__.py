import asyncio
import logging
import signal
from dotenv import load_dotenv
from discord_tracker.config import Config
from discord_tracker.storage.database import Database
from discord_tracker.bot.client import Tracker


async def run(config):
    db = Database(config.database)
    bot = Tracker(config, db)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
        except NotImplementedError:
            pass  # Windows console cancellation is handled by asyncio.run.
    try:
        async with bot:
            await bot.start(config.token)
    finally:
        db.close()


def main():
    load_dotenv(override=False)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    try:
        config = Config.load()
        asyncio.run(run(config))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Avoid traceback URLs containing the bot or interaction token.
        raise SystemExit(f'Startup/runtime failure: {type(exc).__name__}. Check configuration and connectivity.') from None


if __name__ == '__main__':
    main()
