import asyncio
import logging
import time
from telethon import TelegramClient, events
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    Document,
    PeerUser,
    PeerChat,
    PeerChannel,
)
from telegram_forwarder.utils import normalize_identifier, get_conf
from telegram_forwarder.formatter import format_message
from telegram_forwarder.discord import send_to_discord

logger = logging.getLogger(__name__)

# For grouping messages in albums
PENDING_GROUPS = {}  # {grouped_id: asyncio.Task}
GROUP_WAIT_TIME = 3.0  # seconds to collect all messages in a group

AVATAR_CACHE = {}
AVATAR_CACHE_TTL = 3600  # 1 hour

# Webhook attachment file size limit (8 MB)
DISCORD_MAX_FILE_SIZE = 8 * 1024 * 1024


async def process_group(grouped_id, outputs, chat_id, client):
    """Wait for GROUP_WAIT_TIME, then combine all messages in the group and send."""
    await asyncio.sleep(GROUP_WAIT_TIME)

    # Retrieve the list of messages for this group (and remove from pending)
    messages = PENDING_GROUPS.pop(grouped_id, None)
    if not messages:
        return

    # Combine data
    combined_text = []
    combined_files = []  # list of (file_bytes, filename, mime_type)

    for msg_data in messages:
        if msg_data.get("content"):
            combined_text.append(msg_data["content"])
        # Each message contributes its files (usually one, but could be more)
        combined_files.extend(msg_data.get("files", []))

    combined_data = messages[0].copy()  # start with first message's data
    combined_data["content"] = "\n\n".join(combined_text)
    combined_data["files"] = combined_files

    await send_to_discords(combined_data, outputs)


async def resolve_inputs(client, inputs_config):
    """Resolve configured inputs to actual Telegram entities, return list of (entity, output_names)."""
    result = []
    if not inputs_config:
        return result

    dialog_by_id = {}
    dialog_by_username = {}

    logger.info("Fetching dialog list to resolve inputs...")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        dialog_by_id[entity.id] = entity
        if hasattr(entity, "username") and entity.username:
            dialog_by_username[entity.username.lower()] = entity

    for name, channel_info in inputs_config.items():
        identifier = channel_info.get("id")
        output_names = channel_info.get("output-to", [])
        if not output_names:
            logger.warning(f"No outputs defined for input '{name}', skipping.")
            continue

        if identifier is None:
            logger.warning(f"Missing 'id' for input entry '{name}', skipping.")
            continue

        norm = normalize_identifier(identifier)
        if norm is None:
            logger.warning(f"Invalid identifier '{identifier}' for '{name}', skipping.")
            continue

        norm_type, norm_val = norm
        if norm_type == "id":
            entity = dialog_by_id.get(norm_val)
        else:
            entity = dialog_by_username.get(norm_val)

        if entity:
            result.append((entity, output_names))
            logger.info(
                f"Monitoring: {entity.title} (ID: {entity.id}) -> outputs: {output_names}"
            )
        else:
            logger.warning(
                f"Could not find channel for '{name}' with identifier '{identifier}'. "
                f"Make sure you are a member and the identifier is correct."
            )
    return result


async def build_channel_outputs_map(client, config, outputs_by_name: dict) -> dict:
    """
    Build a mapping from chat ID to list of output configurations.
    Uses resolve_inputs and outputs_by_name.
    """
    inputs = config.get("inputs", {})
    channel_outputs = await resolve_inputs(client, inputs)

    channel_outputs_map = {}
    for entity, output_names in channel_outputs:
        outputs = [
            outputs_by_name[name] for name in output_names if name in outputs_by_name
        ]
        if outputs:
            channel_outputs_map[entity.id] = outputs
        else:
            logger.warning(
                f"No valid outputs found for channel {entity.title}, skipping."
            )

    if not channel_outputs_map:
        logger.warning(
            "No valid channels with outputs – the event handler will not receive any messages."
        )
    return channel_outputs_map


def peer_to_id(peer):
    """Extract integer ID from a Peer object."""
    if isinstance(peer, PeerUser):
        return peer.user_id
    if isinstance(peer, PeerChat):
        return peer.chat_id
    if isinstance(peer, PeerChannel):
        return peer.channel_id
    return None


async def get_entity_avatar_bytes(client, entity):
    """Return cached avatar bytes for any entity (User, Chat, Channel)."""
    entity_id = entity.id
    now = time.time()
    if entity_id in AVATAR_CACHE:
        cached_bytes, timestamp = AVATAR_CACHE[entity_id]
        if now - timestamp < AVATAR_CACHE_TTL:
            return cached_bytes

    try:
        avatar_bytes = await client.download_profile_photo(entity, file=bytes)
        if avatar_bytes:
            AVATAR_CACHE[entity_id] = (avatar_bytes, now)
            return avatar_bytes
    except Exception as e:
        logger.warning(f"Could not download avatar for {entity_id}: {e}")

    return None


def is_video_media(media):
    """
    Return True if the media is a video (MessageMediaDocument with video mime type
    or a document with video attribute).
    """
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if isinstance(doc, Document):
            # Check mime type
            if doc.mime_type and doc.mime_type.startswith("video/"):
                return True
            # Also check if it has video attribute (some video documents)
            if hasattr(doc, "video") and doc.video:
                return True
    return False


async def prepare_message_data(client, event):
    chat = await event.get_chat()
    # Webhook username is the chat (group/channel) name
    webhook_username = chat.title if hasattr(chat, "title") else str(chat.id)

    # Determine original sender
    sender_entity = None
    if hasattr(event.message, "from_id") and event.message.from_id:
        sender_id = peer_to_id(event.message.from_id)
        if sender_id:
            try:
                sender_entity = await client.get_entity(sender_id)
            except Exception as e:
                logger.debug(f"Could not get sender entity {sender_id}: {e}")
    # If no sender, use the chat as the sender (for channels, or messages without sender)
    if not sender_entity:
        sender_entity = chat

    # Extract forward info and forward entity
    forward_info = None
    forward_entity = None
    if event.message.forward:
        fwd = event.message.forward
        # Get forward title if from a channel
        if fwd.chat and fwd.chat.title:
            forward_info = fwd.chat.title
        # Try to get the original sender entity
        peer = getattr(fwd, "from_id", None)
        if peer:
            fwd_id = peer_to_id(peer)
            if fwd_id:
                try:
                    forward_entity = await client.get_entity(fwd_id)
                except Exception as e:
                    logger.debug(f"Could not get forward entity: {e}")
        # If still no entity, try sender_name (user‑forwarded message)
        if not forward_entity and hasattr(fwd, "sender_name"):
            forward_info = fwd.sender_name

    if forward_entity:
        author_entity = forward_entity
    else:
        author_entity = sender_entity

    # Build author name
    if hasattr(author_entity, "title"):
        author_name = author_entity.title
    elif hasattr(author_entity, "first_name"):
        author_name = author_entity.first_name
        if hasattr(author_entity, "last_name") and author_entity.last_name:
            author_name += f" {author_entity.last_name}"
        if not author_name.strip():
            author_name = author_entity.username or str(author_entity.id)
    else:
        author_name = str(author_entity.id)

    # Attach avatar for author entity
    avatar_bytes = await get_entity_avatar_bytes(client, author_entity)
    avatar_filename = f"avatar_{author_entity.id}.jpg" if avatar_bytes else None

    # Handle text
    raw_text = event.message.raw_text or ""
    entities = event.message.entities
    formatted_text = format_message(raw_text, entities)

    # Start with content as formatted text
    content = formatted_text

    # Download media if present
    async def _download_media_checked(
        media_obj, max_size, default_filename, default_mime, size_hint=None
    ):
        """
        Downloads media if it fits within max_size.
        Returns (file_bytes, filename, mime_type, omitted_mb)
        where omitted_mb is None if successful, otherwise the size in MB.
        """
        # Pre‑check using size hint if available
        if size_hint is not None and size_hint > max_size:
            return (None, None, None, size_hint / (1024 * 1024))

        try:
            file_bytes = await client.download_media(media_obj, bytes)
            if file_bytes and len(file_bytes) <= max_size:
                return (file_bytes, default_filename, default_mime, None)
            else:
                size_mb = len(file_bytes) / (1024 * 1024) if file_bytes else 0
                return (None, None, None, size_mb)
        except Exception as e:
            logger.error(f"Failed to download {default_filename}: {e}")
            return (None, None, None, None)

    # Helper to add omission note
    def add_omission_note(media_type, size_mb):
        nonlocal content
        note = f"\n\n*({media_type} – {size_mb:.1f} MB)*"
        content = (content + note) if content else note.strip()

    files = []  # list of (file_bytes, filename, mime_type)
    media = event.message.media
    if media:
        if isinstance(media, MessageMediaPhoto):
            size_hint = (
                getattr(media.photo, "size", None) if hasattr(media, "photo") else None
            )
            file_bytes, filename, mime_type, omitted_mb = await _download_media_checked(
                media,
                DISCORD_MAX_FILE_SIZE,
                f"photo_{event.message.id}.jpg",
                "image/jpeg",
                size_hint=size_hint,
            )
            if file_bytes:
                files.append((file_bytes, filename, mime_type))
            elif omitted_mb is not None:
                add_omission_note("Photo", omitted_mb)

        elif isinstance(media, MessageMediaDocument):
            doc = media.document
            size_hint = getattr(doc, "size", None) if doc else None
            is_video = (
                doc and doc.mime_type and doc.mime_type.startswith("video/")
            ) or (hasattr(doc, "video") and doc.video)

            # Determine filename (same as before)
            original_filename = getattr(doc, "file_name", None)
            if not original_filename and doc and hasattr(doc, "attributes"):
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        original_filename = attr.file_name
                        break

            if original_filename:
                if len(original_filename) > 255:
                    original_filename = original_filename[:255]
                filename = original_filename
            else:
                # Generate a filename
                if is_video:
                    ext = "mp4"
                    if doc and doc.mime_type:
                        ext = (
                            doc.mime_type.split("/")[-1]
                            if "/" in doc.mime_type
                            else "mp4"
                        )
                    filename = f"video_{event.message.id}.{ext}"
                else:
                    ext = ""
                    if doc and doc.mime_type:
                        mime = doc.mime_type
                        if mime.startswith("image/"):
                            ext = mime.split("/")[-1]
                        elif mime.startswith("video/"):
                            ext = mime.split("/")[-1]
                    filename = (
                        f"file_{event.message.id}.{ext}"
                        if ext
                        else f"file_{event.message.id}"
                    )

            mime_type = doc.mime_type if doc else None

            file_bytes, _, _, omitted_mb = await _download_media_checked(
                media, DISCORD_MAX_FILE_SIZE, filename, mime_type, size_hint=size_hint
            )
            if file_bytes:
                files.append((file_bytes, filename, mime_type))
            elif omitted_mb is not None:
                media_type = "Video" if is_video else "File"
                add_omission_note(media_type, omitted_mb)

    return {
        "message_id": event.message.id,
        "webhook_username": webhook_username,
        "forward_info": forward_info,
        "author_name": author_name,
        "author_avatar_bytes": avatar_bytes,
        "author_avatar_filename": avatar_filename,
        "content": content,
        "files": files,
        "message_link": "https://telegram.org/",  # Just use Telegram's link as source, used to group embeds
    }


async def send_to_discords(data: dict, outputs: list):
    """Send a message to all outputs concurrently."""
    tasks = []
    for out in outputs:
        tasks.append(
            send_to_discord(out["webhook_url"], data, out.get("embed_color", "03b2f8"))
        )
    if tasks:
        await asyncio.gather(*tasks)


async def start_telegram_client(config, outputs_by_name):
    """Start the Telegram client and set up event handling."""

    client = TelegramClient(
        get_conf(config, "instance-name"),
        get_conf(config, "app-id"),
        get_conf(config, "app-hash"),
    )

    try:
        await client.start()
    except Exception as e:
        logger.critical(f"Couldn't start Telegram client: {e}")
        raise

    # Build initial mapping
    channel_outputs_map = await build_channel_outputs_map(
        client, config, outputs_by_name
    )
    client._channel_outputs_map = channel_outputs_map

    @client.on(events.NewMessage)
    async def handler(event):
        chat = await event.get_chat()
        outputs = client._channel_outputs_map.get(chat.id)
        if not outputs:
            return

        grouped_id = getattr(event.message, "grouped_id", None)

        # If this message belongs to a group, collect it and schedule a combined send
        if grouped_id is not None:
            # Prepare message data (downloads the media)
            msg_data = await prepare_message_data(client, event)
            if msg_data is None:
                return

            # Store the message data for this group
            if grouped_id not in PENDING_GROUPS:
                # First message of the group: create a list and schedule a task
                PENDING_GROUPS[grouped_id] = [msg_data]
                # Schedule processing after a short delay
                asyncio.create_task(process_group(grouped_id, outputs, chat.id, client))
            else:
                # Append to existing group list
                PENDING_GROUPS[grouped_id].append(msg_data)
        else:
            # Single message: send immediately
            data = await prepare_message_data(client, event)
            if data is not None:
                await send_to_discords(data, outputs)

    return client
