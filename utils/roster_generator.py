import os
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "arialbd.ttf",
            "arial.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "arial.ttf",
        ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

def generate_roster_image(title: str, headers: list[str], data: list[dict], output_path: str = None) -> BytesIO:
    font_main = _load_font(16)
    font_header = _load_font(18, bold=True)
    font_title = _load_font(22, bold=True)
    col_widths = [180, 250, 110, 230, 280, 160]
    total_width = sum(col_widths)
    row_height = 35
    total_rows = 1 
    total_rows += 1 
    for item in data:
        total_rows += 1
    img_height = total_rows * row_height + 40 
    img_width = total_width + 40
    img = Image.new("RGB", (img_width, img_height), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    x_start = 20
    try:
        bbox = draw.textbbox((0, 0), title, font=font_title)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 200
    draw.text((img_width//2 - tw//2, y), title, fill="black", font=font_title)
    y += row_height + 10
    x = x_start
    draw.rectangle([x, y, x + total_width, y + row_height], fill=(173, 216, 230), outline="black")
    for i, h in enumerate(headers):
        draw.rectangle([x, y, x + col_widths[i], y + row_height], outline="black")
        try:
            bbox = draw.textbbox((0, 0), h, font=font_header)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = 50, 10
        draw.text((x + col_widths[i]//2 - tw//2, y + row_height//2 - th//2 - 2), h, fill="black", font=font_header)
        x += col_widths[i]
    y += row_height
    for item in data:
        x = x_start
        if item.get("is_divider"):
            bg_color = item.get("color", (255, 150, 150))
            draw.rectangle([x, y, x + total_width, y + row_height], fill=bg_color, outline="black")
            text = item.get("title", "")
            try:
                bbox = draw.textbbox((0, 0), text, font=font_header)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw, th = 50, 10
            draw.text((img_width//2 - tw//2, y + row_height//2 - th//2 - 2), text, fill="black", font=font_header)
        else:
            bg_color = item.get("bg_color", (255, 255, 255))
            row_data = item.get("row", [""] * len(col_widths))
            for i, cell in enumerate(row_data):
                draw.rectangle([x, y, x + col_widths[i], y + row_height], fill=bg_color, outline="black")
                text = str(cell)
                if len(text) > 30: text = text[:27] + "..."
                try:
                    bbox = draw.textbbox((0, 0), text, font=font_main)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                except Exception:
                    tw, th = 50, 10
                draw.text((x + col_widths[i]//2 - tw//2, y + row_height//2 - th//2 - 2), text, fill="black", font=font_main)
                x += col_widths[i]
        y += row_height
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    if output_path:
        img.save(output_path)
    return buffer

_LEADERSHIP_RANKS = frozenset({"майор", "подполковник"})
_POSITION_PRIORITY = (
    "начальник цппс",
    "заместитель начальника цппс",
    "помощник начальника цппс",
    "начальник курс",
    "старший преподаватель",
    "преподаватель",
    "инструктор",
    "секретарь",
    "стажёр",
)


def is_course_chief(role_names: list[str]) -> bool:
    for name in role_names:
        folded = name.casefold()
        if "начальник" in folded and "курс" in folded:
            return True
    return False


def cpps_bucket(rank: str, role_names: list[str]) -> str:
    if (rank or "").casefold().strip() in _LEADERSHIP_RANKS:
        return "leadership"
    if is_course_chief(role_names):
        return "senior"
    return "teachers"


def cpps_position_label(role_names: list[str]) -> str:
    matches = []
    for name in role_names:
        folded = name.casefold()
        if "цппс" in folded or ("начальник" in folded and "курс" in folded):
            matches.append(name)
    if not matches:
        return ""

    def score(label: str) -> int:
        folded = label.casefold()
        if "начальник" in folded and "курс" in folded:
            return 3
        for index, token in enumerate(_POSITION_PRIORITY):
            if token in folded:
                return index
        return len(_POSITION_PRIORITY)

    matches.sort(key=score)
    return matches[0]


def _roster_line(mention: str, name: str, rank: str, position: str, static_id: str) -> str:
    shown_name = (name or "").strip() or "Без имени"
    shown_rank = (rank or "").strip() or "—"
    shown_position = (position or "").strip() or "—"
    static = (static_id or "").strip()
    if not static or static in {"Не указан", "—", "-"}:
        static = "—"
    return f"- {mention} · **{shown_name}** — {shown_rank} · {shown_position} · `{static}`"


def _section_text(title: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "- никого"
    return f"### {title}\n{body}"


def _split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        piece = line + "\n"
        if current and size + len(piece) > limit:
            chunks.append("".join(current).rstrip())
            current = [piece]
            size = len(piece)
        else:
            current.append(piece)
            size += len(piece)
    if current:
        chunks.append("".join(current).rstrip())
    return chunks


def build_cpps_roster_components(total: int, sections: list[tuple[str, list[str]]]):
    import disnake
    blocks = [
        disnake.ui.TextDisplay(
            f"# Состав ЦППС\nСотрудников в отделе: **{total}**"
        ),
        disnake.ui.Separator(divider=True, spacing=disnake.SeparatorSpacing.small),
    ]
    for index, (title, lines) in enumerate(sections):
        if index:
            blocks.append(disnake.ui.Separator(divider=True, spacing=disnake.SeparatorSpacing.large))
        for chunk in _split_text(_section_text(title, lines)):
            blocks.append(disnake.ui.TextDisplay(chunk))
    return [
        disnake.ui.Container(
            *blocks,
            accent_colour=disnake.Colour(0x2C2F33),
        )
    ]


def _is_roster_message(message) -> bool:
    if getattr(message.flags, "is_components_v2", False):
        return True
    if (message.content or "").strip() == "Состав ЦППС":
        return True
    return any(getattr(attachment, "filename", "") == "roster.png" for attachment in message.attachments)


async def update_cpps_roster(guild):
    import logging
    import disnake
    from config.constants import settings
    from database import get_user
    from utils.helpers import get_member_rank_index

    logger = logging.getLogger(__name__)
    channel = guild.get_channel(settings.roster_channel_id)
    if channel is None and settings.roster_channel_id:
        try:
            channel = await guild.fetch_channel(settings.roster_channel_id)
        except Exception:
            channel = None
    if not channel:
        return
    cpps_role_id = settings.department_role_ids.get("ЦППС", 0)
    if not cpps_role_id:
        return
    cpps_role = guild.get_role(cpps_role_id)
    if not cpps_role:
        return

    grouped = {"leadership": [], "senior": [], "teachers": []}
    for member in cpps_role.members:
        if getattr(member, "bot", False):
            continue
        db_user = get_user(member.id) or {}
        rank_index = get_member_rank_index(member, guild)
        if rank_index >= 0:
            rank = settings.ranks[rank_index]
        else:
            rank = (db_user.get("rank") or "").strip()
        role_names = [role.name for role in member.roles]
        bucket = cpps_bucket(rank, role_names)
        position = cpps_position_label(role_names)
        if not position and bucket == "senior":
            position = "Начальник курса"
        elif not position and bucket == "teachers":
            position = "Преподаватель"
        personal = (db_user.get("nickname") or "").strip()
        if not personal or personal == "Не указан":
            personal = member.display_name
        static_id = (db_user.get("static_id") or "").strip()
        grouped[bucket].append({
            "rank_index": rank_index,
            "name": personal,
            "line": _roster_line(member.mention, personal, rank, position, static_id),
        })

    for people in grouped.values():
        people.sort(key=lambda item: (-item["rank_index"], item["name"].casefold()))

    leadership = grouped["leadership"]
    senior = grouped["senior"]
    teachers = grouped["teachers"]
    total = len(leadership) + len(senior) + len(teachers)
    components = build_cpps_roster_components(total, [
        (f"Руководство · {len(leadership)}", [item["line"] for item in leadership]),
        (f"Старший состав · {len(senior)}", [item["line"] for item in senior]),
        (f"Преподаватели · {len(teachers)}", [item["line"] for item in teachers]),
    ])
    mentions = disnake.AllowedMentions.none()
    try:
        latest = None
        stale = []
        async for message in channel.history(limit=30):
            if message.author.id != guild.me.id or not _is_roster_message(message):
                continue
            if latest is None and getattr(message.flags, "is_components_v2", False):
                latest = message
            else:
                stale.append(message)
        if latest is not None:
            try:
                await latest.edit(components=components, allowed_mentions=mentions)
            except Exception:
                logger.exception("Не удалось обновить состав ЦППС, отправляю заново")
                await channel.send(components=components, allowed_mentions=mentions)
                stale.append(latest)
        else:
            await channel.send(components=components, allowed_mentions=mentions)
        for message in stale:
            try:
                await message.delete()
            except Exception:
                pass
    except Exception:
        logger.exception("Не удалось отправить состав ЦППС")


def _format_blacklist_until(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "бессрочно"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return f"<t:{int(moment.timestamp())}:D>"


def _blacklist_line(user_id: int, nickname: str, static_id: str, reason: str, expires_at: str | None) -> str:
    name = (nickname or "").strip() or "Без имени"
    static = (static_id or "").strip()
    if not static or static in {"Не указан", "—", "-"}:
        static = "—"
    why = " ".join((reason or "—").split())
    if len(why) > 80:
        why = why[:77] + "..."
    return f"- <@{user_id}> · **{name}** — `{static}` · {_format_blacklist_until(expires_at)} · {why}"


_BLACKLIST_PAGE_LIMIT = 3700


def _blacklist_pages(lines: list[str]) -> list[list[str]]:
    if not lines:
        return [[]]
    pages: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        piece = len(line) + 1
        if current and size + piece > _BLACKLIST_PAGE_LIMIT:
            pages.append(current)
            current = [line]
            size = piece
        else:
            current.append(line)
            size += piece
    if current:
        pages.append(current)
    return pages


def build_blacklist_roster_components(lines: list[str]):
    import disnake
    pages = _blacklist_pages(lines)
    page_count = len(pages)
    messages = []
    for index, page_lines in enumerate(pages, start=1):
        if page_count == 1:
            header = f"# Чёрный список\nЧеловек в списке: **{len(lines)}**"
        else:
            header = f"# Чёрный список\nЧеловек в списке: **{len(lines)}** · {index}/{page_count}"
        body = "\n".join(page_lines) if page_lines else "- никого"
        messages.append([
            disnake.ui.Container(
                disnake.ui.TextDisplay(header),
                disnake.ui.Separator(divider=True, spacing=disnake.SeparatorSpacing.small),
                disnake.ui.TextDisplay(body),
                accent_colour=disnake.Colour(0x2C2F33),
            )
        ])
    return messages


async def update_blacklist_roster(guild):
    import logging
    import disnake
    from config.constants import settings
    from database import get_blacklist

    logger = logging.getLogger(__name__)
    channel_id = settings.blacklist_roster_channel_id
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            channel = None
    if not channel:
        return

    rows = get_blacklist()
    dated = []
    permanent = []
    for user_id, nickname, static_id, reason, _added_by, _created_at, expires_at in rows:
        line = _blacklist_line(user_id, nickname, static_id, reason, expires_at)
        if (expires_at or "").strip():
            dated.append(((expires_at or ""), line))
        else:
            permanent.append(((nickname or "").casefold(), line))
    dated.sort(key=lambda item: item[0])
    permanent.sort(key=lambda item: item[0])
    lines = [line for _, line in dated] + [line for _, line in permanent]
    pages = build_blacklist_roster_components(lines)
    mentions = disnake.AllowedMentions.none()
    try:
        existing = []
        async for message in channel.history(limit=50):
            if message.author.id != guild.me.id:
                continue
            if getattr(message.flags, "is_components_v2", False):
                existing.append(message)
        existing.reverse()
        for index, components in enumerate(pages):
            if index < len(existing):
                await existing[index].edit(components=components, allowed_mentions=mentions)
            else:
                await channel.send(components=components, allowed_mentions=mentions)
        for message in existing[len(pages):]:
            try:
                await message.delete()
            except Exception:
                pass
    except Exception:
        logger.exception("Не удалось отправить чёрный список")