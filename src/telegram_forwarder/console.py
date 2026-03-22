import asyncio
import logging

logger = logging.getLogger(__name__)


async def console_loop(client, extra_commands=None):
    """
    Handle user input from the console.
    extra_commands can be a dict mapping command name to either:
      - a callable, or
      - a dict with keys 'func' (callable) and 'desc' (optional description)
    """

    async def _show_help():
        lines = ["\nAvailable commands:"]
        for name, info in commands.items():
            desc = info.get("desc", "")
            if desc:
                lines.append(f"  {name} - {desc}")
            else:
                lines.append(f"  {name}")
        lines.append("")
        logger.info("\n".join(lines))

    async def _stop_client():
        logger.info("Stop command received, disconnecting...")
        await client.disconnect()

    # Built-in commands with descriptions
    commands = {
        "help": {"func": _show_help, "desc": "show this help message"},
        "stop": {"func": _stop_client, "desc": "disconnect the client and exit"},
    }

    # Merge extra commands
    if extra_commands:
        for name, cmd in extra_commands.items():
            if isinstance(cmd, dict) and "func" in cmd:
                commands[name] = cmd
            else:
                # Assume callable, no description
                commands[name] = {"func": cmd, "desc": ""}

    while True:
        command = await asyncio.to_thread(input, "> ")
        command = command.strip().lower()
        if command in commands:
            await commands[command]["func"]()
            if command == "stop":
                break
        else:
            logger.info(
                f"Unknown command '{command}'. Type 'help' for available commands."
            )
