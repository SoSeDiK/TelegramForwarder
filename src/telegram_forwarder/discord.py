import asyncio
import json
import logging
from typing import List, Tuple, Union
from discord_webhook import AsyncDiscordWebhook, DiscordEmbed

logger = logging.getLogger(__name__)

# Discord limits
EMBED_DESCRIPTION_MAX = 4096
EMBED_AUTHOR_MAX = 256
MAX_EMBEDS_PER_MESSAGE = 10
MAX_ATTACHMENTS = 10

# Supported embed image MIME types (Discord will display them inline)
SUPPORTED_EMBED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")

# Retry settings
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
REQUEST_TIMEOUT = 30.0  # seconds


async def send_to_discord(
    webhook_url: str, data: dict, embed_color: str = "03b2f8"
) -> None:
    """
    Send a message to Discord via a webhook, with support for text, files, and retries.

    The `data` dictionary should contain the following keys:
        - webhook_username (str): Name shown as the webhook author.
        - author_name (str, optional): Name shown in the embed author. Defaults to webhook_username.
        - content (str): The main text content (truncated to 4096 chars).
        - message_id (int, optional): Used for logging.
        - forward_info (str, optional): Appended to the author name if present.
        - files (list[tuple[bytes, str, str]], optional): List of (file_bytes, filename, mime_type) for all files.
        - message_link (str, optional): URL to set as embed's link.
        - author_avatar_bytes (bytes, optional): Avatar image bytes.
        - author_avatar_filename (str, optional): Avatar filename.

    For multiple files, the first file is attached to the main embed. If its MIME type starts with 'image/',
    it is also set as the embed image. Additional image files become separate embeds (up to MAX_EMBEDS_PER_MESSAGE - 1).
    Non‑image files are simply attached and do not create extra embeds.

    The function includes retry logic for rate limits (429) and server errors (5xx):
        - For 429, respects the `retry_after` header if present.
        - For 5xx and timeouts, uses exponential backoff up to MAX_RETRIES.

    Args:
        webhook_url (str): Discord webhook URL.
        data (dict): Message data as described above.
        embed_color (str): Hex color code for the embed (default "03b2f8").
    """
    # Build webhook name and author
    webhook_name = data["webhook_username"]
    author = data.get("author_name", webhook_name)
    if data.get("forward_info"):
        if author == data["forward_info"]:
            author = "↪ " + author
        else:
            author += f" (↪ {data['forward_info']})"

    if len(author) > EMBED_AUTHOR_MAX:
        author = author[: EMBED_AUTHOR_MAX - 1] + "…"

    # Truncate content if needed
    content = data["content"]
    if len(content) > EMBED_DESCRIPTION_MAX:
        content = content[: EMBED_DESCRIPTION_MAX - 1] + "…"

    message_link = data.get("message_link")

    # Prepare webhook
    webhook = AsyncDiscordWebhook(url=webhook_url, username=webhook_name)

    # Track number of attachments used
    attachments_used = 0

    # Attach avatar if available (consumes 1 attachment)
    if data.get("author_avatar_bytes") and data.get("author_avatar_filename"):
        webhook.add_file(
            file=data["author_avatar_bytes"], filename=data["author_avatar_filename"]
        )
        avatar_url = f"attachment://{data['author_avatar_filename']}"
        attachments_used += 1
    else:
        avatar_url = None

    main_embed = None
    extra_embeds = []

    # Helper to check remaining attachment slots
    def has_attachment_slots(needed=1):
        return attachments_used + needed <= MAX_ATTACHMENTS

    # Handle files
    files = data.get("files", [])
    if files:
        # Create the main embed (will be used even if no files are attached due to limit)
        main_embed = DiscordEmbed(description=content, color=embed_color)
        # Process files in order, but stop when we hit attachment limit
        for i, (file_bytes, filename, mime_type) in enumerate(files):
            if not has_attachment_slots():
                logger.warning(
                    f"Attachment limit reached ({MAX_ATTACHMENTS}). Skipping remaining files. "
                    f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
                )
                break

            webhook.add_file(file=file_bytes, filename=filename)
            attachments_used += 1

            if i == 0:
                # Image preview for main embed
                if mime_type in SUPPORTED_EMBED_IMAGE_TYPES:
                    main_embed.set_image(url=f"attachment://{filename}")
            else:
                # Include extra images as extra embeds
                if mime_type in SUPPORTED_EMBED_IMAGE_TYPES:
                    # Check if we still have room for an extra embed (max 10 embeds)
                    if len(extra_embeds) < MAX_EMBEDS_PER_MESSAGE - 1:
                        extra_embed = DiscordEmbed(description="", color=embed_color)
                        extra_embed.set_image(url=f"attachment://{filename}")
                        extra_embed.set_url(message_link)
                        extra_embeds.append(extra_embed)
                    else:
                        logger.warning(
                            f"Too many image files: {len(files)}. Only first {MAX_EMBEDS_PER_MESSAGE} will be sent. "
                            f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
                        )

        webhook.add_embed(main_embed)
        for embed in extra_embeds:
            webhook.add_embed(embed)

    else:
        # No files: just a simple embed
        main_embed = DiscordEmbed(description=content, color=embed_color)
        webhook.add_embed(main_embed)

    # Set author and URL for the main embed (if it exists)
    if main_embed:
        if avatar_url:
            main_embed.set_author(name=author, icon_url=avatar_url)
        else:
            main_embed.set_author(name=author)
        main_embed.set_url(message_link)

    # Retry logic (unchanged)
    for attempt in range(MAX_RETRIES):
        try:
            response = await asyncio.wait_for(
                webhook.execute(), timeout=REQUEST_TIMEOUT
            )

            if 200 <= response.status_code < 300:
                return

            # Rate limit
            if response.status_code == 429:
                retry_after = BASE_DELAY * (2**attempt)
                try:
                    error_data = response.json()
                    retry_after = float(error_data.get("retry_after", retry_after))
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
                logger.warning(
                    f"Rate limited (429) on attempt {attempt+1}/{MAX_RETRIES}. "
                    f"Waiting {retry_after:.2f}s. "
                    f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
                )
                await asyncio.sleep(retry_after)
                continue

            # Server errors
            if 500 <= response.status_code < 600:
                wait = BASE_DELAY * (2**attempt)
                logger.warning(
                    f"Server error {response.status_code} on attempt {attempt+1}/{MAX_RETRIES}. "
                    f"Waiting {wait:.2f}s. "
                    f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
                )
                await asyncio.sleep(wait)
                continue

            # Other 4xx errors are not retried
            logger.error(
                f"Discord webhook returned {response.status_code}: {response.text} "
                f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
            )
            return

        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout on attempt {attempt+1}/{MAX_RETRIES}. "
                f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
            )
            if attempt < MAX_RETRIES - 1:
                wait = BASE_DELAY * (2**attempt)
                await asyncio.sleep(wait)
                continue
            else:
                logger.error(
                    f"All {MAX_RETRIES} attempts timed out. "
                    f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
                )
                return

        except Exception as e:
            logger.exception(
                f"Exception on attempt {attempt+1}/{MAX_RETRIES}: {e} "
                f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
            )
            if attempt < MAX_RETRIES - 1:
                wait = BASE_DELAY * (2**attempt)
                await asyncio.sleep(wait)
                continue
            else:
                return

    # All tries failed, just log :(
    logger.error(
        f"All {MAX_RETRIES} retries exhausted. "
        f"Channel: {webhook_name} | Message ID: {data.get('message_id')}"
    )
