import bisect
from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityUnderline,
    MessageEntityStrike,
    MessageEntitySpoiler,
    MessageEntityBlockquote,
    MessageEntityMention,
    MessageEntityTextUrl,
    MessageEntityMentionName,
    MessageEntityPre,
    MessageEntityCode,
)


def build_utf16_offsets(text: str):
    """Return a list of cumulative UTF‑16 code unit counts for each character."""
    offsets = [0]
    for ch in text:
        code_units = 2 if ord(ch) > 0xFFFF else 1
        offsets.append(offsets[-1] + code_units)
    return offsets


def convert_entity_offsets(text: str, entities):
    """Convert entity offsets from UTF‑16 code units to Unicode code points."""
    offsets = build_utf16_offsets(text)
    for e in entities:
        start_idx = bisect.bisect_right(offsets, e.offset) - 1
        if start_idx < 0:
            start_idx = 0
        end_idx = bisect.bisect_left(offsets, e.offset + e.length)
        e.offset = start_idx
        e.length = end_idx - start_idx
    return entities


def expand_to_lines(text: str, start: int, end: int):
    """Expand a range to include complete lines."""
    line_start = text.rfind("\n", 0, start) + 1 if "\n" in text[:start] else 0
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return line_start, line_end


def apply_inline_formatting(content: str, entity) -> str:
    """Apply a single non‑blockquote entity's formatting to the given content."""
    if isinstance(entity, MessageEntityBold):
        return f"**{content}**"
    if isinstance(entity, MessageEntityItalic):
        return f"*{content}*"
    if isinstance(entity, MessageEntityUnderline):
        return f"__{content}__"
    if isinstance(entity, MessageEntityStrike):
        return f"~~{content}~~"
    if isinstance(entity, MessageEntitySpoiler):
        return f"||{content}||"
    if isinstance(entity, MessageEntityCode):
        return f"`{content}`"
    if isinstance(entity, MessageEntityPre):
        return f"```\n{content}\n```"
    if isinstance(entity, MessageEntityMention):
        username = content.lstrip("@")
        return f"[@{username}](https://t.me/{username})"
    if isinstance(entity, MessageEntityTextUrl):
        return f"[{content}]({entity.url})"
    if isinstance(entity, MessageEntityMentionName):
        return f"[{content}](tg://user?id={entity.user_id})"
    return content


def is_wrapper(entity) -> bool:
    """Return True if the entity is a formatting wrapper, not a replacement."""
    return not isinstance(
        entity, (MessageEntityMention, MessageEntityTextUrl, MessageEntityMentionName)
    )


def is_blockquote(entity) -> bool:
    return isinstance(entity, MessageEntityBlockquote)


def format_message(text: str, entities) -> str:
    """Convert Telegram message with entities to Discord markdown."""
    if not entities or not text:
        return text or ""

    # Convert UTF‑16 offsets to code point indices
    entities = convert_entity_offsets(text, entities)

    # Expand blockquotes to full lines
    for e in entities:
        if is_blockquote(e):
            start, end = expand_to_lines(text, e.offset, e.offset + e.length)
            e.offset = start
            e.length = end - start

    # Build events (open/close at each boundary)
    events = []
    for e in entities:
        events.append((e.offset, "start", e))
        events.append((e.offset + e.length, "end", e))

    # Sort: first by position, then end before start at same position
    events.sort(key=lambda x: (x[0], 0 if x[1] == "end" else 1))

    # Segment the text
    active = []  # all currently active entities
    segments = []  # (segment_text, active_wrappers, active_blockquotes)
    last_pos = 0

    for pos, typ, e in events:
        if pos > last_pos:
            seg_text = text[last_pos:pos]
            if seg_text:
                # Separate wrappers (including links) from blockquotes
                wrappers = [e for e in active if not is_blockquote(e)]
                blockquotes = [e for e in active if is_blockquote(e)]
                segments.append((seg_text, wrappers.copy(), blockquotes.copy()))
        if typ == "start":
            active.append(e)
        else:
            active.remove(e)
        last_pos = pos

    if last_pos < len(text):
        seg_text = text[last_pos:]
        if seg_text:
            wrappers = [e for e in active if not is_blockquote(e)]
            blockquotes = [e for e in active if is_blockquote(e)]
            segments.append((seg_text, wrappers.copy(), blockquotes.copy()))

    # First, apply all inline formatting (non‑blockquote) to each segment
    formatted_segments = []
    for seg_text, wrappers, blockquotes in segments:
        # Sort wrappers by length ascending (innermost first)
        wrappers.sort(key=lambda e: e.length)
        formatted = seg_text
        for e in wrappers:
            formatted = apply_inline_formatting(formatted, e)
        formatted_segments.append((formatted, blockquotes))

    # Merge consecutive segments with the same blockquote stack
    merged = []
    for formatted, blockquotes in formatted_segments:
        if not merged or merged[-1][1] != blockquotes:
            merged.append([formatted, blockquotes])
        else:
            merged[-1][0] += formatted

    # Apply blockquotes from innermost to outermost
    final_parts = []
    for text, blockquotes in merged:
        # Apply blockquotes in reverse order (innermost first)
        for bq in reversed(blockquotes):
            lines = text.split("\n")
            text = "\n".join("> " + line for line in lines)
        final_parts.append(text)

    return "".join(final_parts)
