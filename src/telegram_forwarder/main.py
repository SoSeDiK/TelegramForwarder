import asyncio
import logging
import sys
from telegram_forwarder.utils import load_yaml_config, build_outputs_by_name
from telegram_forwarder.telegram import start_telegram_client, build_channel_outputs_map
from telegram_forwarder.console import console_loop

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def start():
    config = await asyncio.to_thread(load_yaml_config, "config.yml")

    outputs_by_name = build_outputs_by_name(config)
    if not outputs_by_name:
        logger.error("No valid outputs defined in config.")
        sys.exit(1)

    client = await start_telegram_client(config, outputs_by_name)

    async def reload_config():
        """Reload config file and update client's channel mapping."""
        try:
            new_config = await asyncio.to_thread(load_yaml_config, "config.yml")
            new_outputs_by_name = build_outputs_by_name(new_config)
            if not new_outputs_by_name:
                logger.error("No valid outputs in reloaded config.")
                return

            new_map = await build_channel_outputs_map(
                client, new_config, new_outputs_by_name
            )
            client._channel_outputs_map = new_map
            logger.info(f"Config reloaded. Now monitoring {len(new_map)} channels.")
        except Exception as e:
            logger.exception("Error reloading config, keeping previous configuration.")

    extra_commands = {
        "reload": {
            "func": reload_config,
            "desc": "reload configuration from config.yml",
        }
    }

    logger.info("Bot started. Type 'help' in the console for commands.")
    try:
        # Run console loop and client concurrently
        await asyncio.gather(
            console_loop(client, extra_commands), client.run_until_disconnected()
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received, disconnecting…")
        raise
    finally:
        # Ensure client is disconnected if not already
        if client and client.is_connected():
            await client.disconnect()

def main():
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down.")
        sys.exit(0)
    except Exception as e:
        logger.exception("Unexpected error")
        sys.exit(1)

if __name__ == "__main__":
    main()
