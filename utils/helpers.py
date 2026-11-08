import re
import time
import datetime
import logging
from urllib.parse import urlparse

import disnake

from config.settings import settings, MIN_MANAGE_RANK, SERGEANT_RANK

logger = logging.getLogger("bot.helpers")

SEPARATE_BATTALION = "1-й Отдельный Батальон"

_ACADEMY_DEPTS = frozenset({"", "нет", "академия гибдд", "академия"})
# БМТО больше нет
_REMOVED_DEPTS = frozenset({"бмто", "мто"})
_LEGACY_BATTALIONS = frozenset({
    "1-й батальон 1 полка",
    "2-й батальон 1 полка",
    "3-й батальон 1 полка",
    "1бп", "2бп", "3бп",
})

def canonical_dept(name: str | None) -> str:
    cleaned = (name or "").strip()
    if cleaned in _LEGACY_BATTALIONS or cleaned.lower() in _LEGACY_BATTALIONS:
        return SEPARATE_BATTALION
    if cleaned.lower() in _REMOVED_DEPTS:
        return "Нет"
    return cleaned or "Нет"

def resolve_department_role_id(name: str | None) -> int | None:
    if not name:
        return None
    canon = canonical_dept(name)
    for key in (name, canon):
        role_id = settings.department_role_ids.get(key)
        if role_id:
            return role_id
    if canon == SEPARATE_BATTALION:
        for legacy in ("1БП", "1-й батальон 1 полка"):
            legacy_id = settings.department_role_ids.get(legacy)
            if legacy_id:
                return legacy_id
    return None

def list_departments() -> list[str]:
    seen: list[str] = []
    for name in settings.department_role_ids:
        # БМТО больше нет
        if name.strip().lower() in _REMOVED_DEPTS:
            continue
        canon = canonical_dept(name)
        if not canon or canon == "Нет" or canon in seen:
            continue
        seen.append(canon)
    return seen

def member_department(member: disnake.Member, guild: disnake.Guild) -> str:
    fallback = None
    for dept_name, role_id in settings.department_role_ids.items():
        # БМТО больше нет
        if dept_name.strip().lower() in _REMOVED_DEPTS:
            continue
        role = guild.get_role(role_id)
        if role and role in member.roles:
            canon = canonical_dept(dept_name)
            if canon == SEPARATE_BATTALION:
                return canon
            if fallback is None:
                fallback = canon
    return fallback or "Нет"

def separate_battalion_pings(dept: str | None) -> str:
    if canonical_dept(dept) != SEPARATE_BATTALION:
        return ""
    parts = []
    if settings.cmdr_1_role_id:
        parts.append(f"<@&{settings.cmdr_1_role_id}>")
    if settings.dep_cmdr_1_role_id:
        parts.append(f"<@&{settings.dep_cmdr_1_role_id}>")
    return " ".join(parts)

_MODAL_CACHE_TTL = 1800
_MODAL_CACHE_MAX_SIZE = 1000

MODAL_CACHE: dict[int, dict] = {}

def _cleanup_modal_cache() -> None:
    now = time.monotonic()
    expired = [
        uid for uid, data in MODAL_CACHE.items()
        if now - data.get("_ts", 0) > _MODAL_CACHE_TTL
    ]
    for uid in expired:
        del MODAL_CACHE[uid]

    if len(MODAL_CACHE) > _MODAL_CACHE_MAX_SIZE:
        sorted_entries = sorted(
            MODAL_CACHE.items(), key=lambda x: x[1].get("_ts", 0)
        )
        to_remove = len(MODAL_CACHE) - _MODAL_CACHE_MAX_SIZE
        for uid, _ in sorted_entries[:to_remove]:
            del MODAL_CACHE[uid]

_BLOCKED_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "ftp"})
_MAX_URL_LENGTH = 2048

def validate_docs_url(url: str) -> bool:
    if not url or not url.strip():
        return False

    url = url.strip()

    if len(url) > _MAX_URL_LENGTH:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    if not parsed.netloc or "." not in parsed.netloc:
        return False

    full_lower = url.lower()
    for scheme in _BLOCKED_SCHEMES:
        if f"{scheme}:" in full_lower:
            return False

    return True

def can_manage_role(bot_member: disnake.Member | None, role: disnake.Role | None) -> bool:
    if not role or not bot_member:
        return False
    return bot_member.top_role.position > role.position

def has_staff_role(member: disnake.Member, guild: disnake.Guild) -> bool:
    role_id = settings.ss_role_id
    if not role_id:
        return False
    staff_role = guild.get_role(role_id)
    if not staff_role:
        return False
    return staff_role in member.roles

def employee_refusal(member, user_db: dict | None, *, self_target: bool) -> str | None:
    employed = bool(user_db) and (user_db.get("status") or "") == "active"
    roles = getattr(member, "roles", ())
    if employed and settings.fired_role_id and any(role.id == settings.fired_role_id for role in roles):
        employed = False
    if employed and settings.base_role_id and not any(role.id == settings.base_role_id for role in roles):
        employed = False
    if employed and member is None:
        employed = False
    if employed:
        return None
    if self_target:
        return "Вы не являетесь сотрудником."
    return "Он не является сотрудником."

def get_member_rank_index(member: disnake.Member, guild: disnake.Guild | None = None) -> int:
    ranks = settings.ranks
    target_guild = guild or getattr(member, "guild", None)
    for idx in range(len(ranks) - 1, -1, -1):
        rank_name = ranks[idx]
        if target_guild:
            role_id = settings.ranks_map.get(rank_name)
            if role_id:
                role = target_guild.get_role(role_id)
                if role and role in member.roles:
                    return idx
        for role in member.roles:
            if role.name.strip().lower() == rank_name.strip().lower():
                return idx
    return -1

def can_manage_staff(member: disnake.Member, guild: disnake.Guild) -> bool:
    if not has_staff_role(member, guild):
        return False
    ranks = settings.ranks
    captain_idx = -1
    for i, r in enumerate(ranks):
        if r == MIN_MANAGE_RANK:
            captain_idx = i
            break
    if captain_idx == -1:
        return False
    return get_member_rank_index(member, guild) >= captain_idx

def is_ss(member: disnake.Member) -> bool:
    if not settings.ss_role_id:
        return False
    return any(r.id == settings.ss_role_id for r in member.roles)

def is_protected_role(role: disnake.Role) -> bool:

    if settings.ss_role_id and role.id == settings.ss_role_id:
        return True

    if role.id in settings.protected_role_ids:
        return True

    dangerous_perms = (
        role.permissions.administrator,
        role.permissions.manage_guild,
        role.permissions.manage_roles,
        role.permissions.ban_members,
    )
    if any(dangerous_perms):
        return True

    return False

def can_manage_applications(member: disnake.Member, guild: disnake.Guild | None = None) -> bool:
    if is_ss(member):
        return True

    target_guild = guild or getattr(member, "guild", None)
    rank_idx = get_member_rank_index(member, target_guild)
    captain_idx = get_rank_index(MIN_MANAGE_RANK)
    if rank_idx != -1 and captain_idx != -1 and rank_idx >= captain_idx:
        return True

    cpps_roles = frozenset({
        "преподаватель", "цппс",
        "начальник цппс", "зам. начальника цппс",
        "заместитель начальника цппс",
    })
    for role in member.roles:
        name_lower = role.name.strip().lower()
        if name_lower in cpps_roles:
            return True

    return False

def can_manage_audit(member: disnake.Member) -> bool:
    return is_ss(member)

def can_manage_resignations(member: disnake.Member) -> bool:
    return is_ss(member)

def get_staff_title(member: disnake.Member, guild: disnake.Guild) -> str:
    ranks = settings.ranks
    for idx in range(len(ranks) - 1, -1, -1):
        rank_name = ranks[idx]
        role_id = settings.ranks_map.get(rank_name)
        if role_id:
            role = guild.get_role(role_id)
            if role and role in member.roles:
                return f"{rank_name} {member.display_name}"
        else:
            for role in member.roles:
                if role.name.strip().lower() == rank_name.strip().lower():
                    return f"{rank_name} {member.display_name}"
    return member.display_name

def get_rank_index(rank_name: str) -> int:
    cleaned = rank_name.strip().lower()
    for i, r in enumerate(settings.ranks):
        if r.strip().lower() == cleaned:
            return i
    return -1

def is_rank_sergeant_or_above(rank_name: str) -> bool:
    sgt_idx = get_rank_index(SERGEANT_RANK)
    rank_idx = get_rank_index(rank_name)
    if sgt_idx == -1 or rank_idx == -1:
        return False
    return rank_idx >= sgt_idx

async def send_dm(user: disnake.abc.User, embed: disnake.Embed = None, components: list = None) -> bool:
    try:
        await user.send(embed=embed, components=components)
        return True
    except (disnake.Forbidden, disnake.HTTPException):
        return False

def plain_name(name: str) -> str:
    text = (name or "").strip()
    if " | " in text:
        text = text.rsplit(" | ", 1)[-1].strip()
    return text or "Сотрудник"

def status_nick(prefix: str, name: str) -> str:
    base = plain_name(name)
    nick = f"{prefix} | {base}"
    if len(nick) <= 32:
        return nick
    room = 32 - len(prefix) - 3
    if room > 0:
        return f"{prefix} | {base[:room]}"
    return prefix[:32]

async def send_notice(channel, text: str) -> None:
    container = disnake.ui.Container(
        disnake.ui.TextDisplay(text[:4000]),
        accent_colour=disnake.Colour(0x2C2F33),
    )
    await channel.send(
        components=[container],
        allowed_mentions=disnake.AllowedMentions(everyone=False, users=True, roles=True),
    )

async def _blacklist_channel(guild: disnake.Guild):
    if not settings.blacklist_channel_id:
        return None
    channel = guild.get_channel(settings.blacklist_channel_id)
    if channel is not None:
        return channel
    try:
        return await guild.fetch_channel(settings.blacklist_channel_id)
    except Exception as exc:
        logger.error("Канал ЧС %s недоступен: %s", settings.blacklist_channel_id, exc)
        return None

async def give_blacklist(
    guild: disnake.Guild,
    member: disnake.Member | None,
    *,
    user_id: int,
    nickname: str,
    static_id: str,
    reason: str,
    duration_str: str,
    added_by: disnake.abc.User,
) -> list[str]:
    from database import add_to_blacklist

    errors: list[str] = []
    raw_duration = (duration_str or "").strip()
    permanent_words = {"", "навсегда", "перманентно", "вечно", "бессрочно", "-", "нет"}
    expires_at = None
    duration_display = raw_duration or "Навсегда"
    if raw_duration.lower() in permanent_words:
        duration_display = "Навсегда"
    else:
        parsed = parse_duration(raw_duration)
        if parsed:
            expires_at = parsed.isoformat()
        else:
            duration_display = "Навсегда"
            errors.append("Срок ЧС не распознан, запись бессрочная")

    add_to_blacklist(
        user_id=user_id,
        nickname=plain_name(nickname),
        static_id=static_id or "Не указан",
        reason=reason,
        added_by_id=added_by.id,
        added_by_name=str(added_by),
        expires_at=expires_at,
    )

    if member is not None:
        bot_member = guild.me
        role = guild.get_role(settings.blacklist_role_id) if settings.blacklist_role_id else None
        if role is None:
            errors.append("Роль ЧС не найдена")
        elif can_manage_role(bot_member, role):
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason="Чёрный список")
            except Exception as exc:
                errors.append(f"Роль ЧС: {exc}")
        else:
            errors.append("Роль ЧС выше роли бота")
        try:
            await member.edit(nick=status_nick("Уволен + ЧС", nickname or member.display_name))
        except Exception as exc:
            errors.append(f"Ник ЧС: {exc}")

    pings = " ".join(f"<@&{role_id}>" for role_id in settings.blacklist_ping_roles if role_id)
    mention = member.mention if member is not None else f"<@{user_id}>"
    text = (
        (f"{pings}\n" if pings else "")
        + "**Чёрный список**\n"
        + f"**Сотрудник:** {plain_name(nickname)} | {static_id or 'Не указан'} | {mention}\n"
        + f"**Выдал:** {getattr(added_by, 'display_name', added_by)} | {added_by.mention}\n"
        + f"**Срок:** {duration_display}\n"
        + f"**Причина:** {reason}"
    )
    channel = await _blacklist_channel(guild)
    if channel is None:
        errors.append("Канал ЧС не найден")
    else:
        try:
            await send_notice(channel, text)
        except Exception as exc:
            logger.error("Не удалось отправить уведомление в ЧС: %s", exc)
            errors.append(f"Канал ЧС: {exc}")
    _schedule_blacklist_roster(guild)
    return errors

async def take_blacklist(
    guild: disnake.Guild,
    user_id: int,
    *,
    reason: str,
    actor: disnake.abc.User | None,
) -> list[str]:
    from database import remove_from_blacklist

    errors: list[str] = []
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None

    if member is not None:
        bot_member = guild.me
        role = guild.get_role(settings.blacklist_role_id) if settings.blacklist_role_id else None
        if role and role in member.roles:
            if can_manage_role(bot_member, role):
                try:
                    await member.remove_roles(role, reason="Снятие ЧС")
                except Exception as exc:
                    errors.append(f"Снять роль ЧС: {exc}")
            else:
                errors.append("Роль ЧС выше роли бота")
        fired = guild.get_role(settings.fired_role_id) if settings.fired_role_id else None
        try:
            if fired and fired in member.roles:
                nick = status_nick("Уволен", member.display_name)
            else:
                nick = plain_name(member.display_name)[:32]
            await member.edit(nick=nick)
        except Exception as exc:
            errors.append(f"Ник: {exc}")

    remove_from_blacklist(user_id)
    mention = member.mention if member is not None else f"<@{user_id}>"
    actor_line = actor.mention if actor is not None else "срок истёк"
    text = (
        "**Снятие с чёрного списка**\n"
        f"**Сотрудник:** {mention}\n"
        f"**Причина:** {reason}\n"
        f"**Инициатор:** {actor_line}"
    )
    channel = await _blacklist_channel(guild)
    if channel is not None:
        try:
            await send_notice(channel, text)
        except Exception as exc:
            logger.error("Не удалось отправить снятие ЧС: %s", exc)
            errors.append(f"Канал ЧС: {exc}")
    elif settings.blacklist_channel_id:
        errors.append("Канал ЧС не найден")
    _schedule_blacklist_roster(guild)
    return errors

def _schedule_blacklist_roster(guild: disnake.Guild) -> None:
    import asyncio
    from utils.roster_generator import update_blacklist_roster

    async def _run() -> None:
        try:
            await update_blacklist_roster(guild)
        except Exception:
            logger.exception("Не удалось обновить состав ЧС")

    asyncio.create_task(_run())

def find_rank_role(guild: disnake.Guild, rank_name: str) -> disnake.Role | None:
    cleaned_name = rank_name.strip().lower()
    aliases = {
        "мл. сержант": "младший сержант",
        "мл.сержант": "младший сержант",
        "мл.сержант полиции": "младший сержант",
        "ст. сержант": "старший сержант",
        "ст.сержант": "старший сержант",
        "ст. прапорщик": "старший прапорщик",
        "ст.прапорщик": "старший прапорщик",
        "мл. лейтенант": "младший лейтенант",
        "мл.лейтенант": "младший лейтенант",
        "ст. лейтенант": "старший лейтенант",
        "ст.лейтенант": "старший лейтенант",
    }
    if cleaned_name in aliases:
        cleaned_name = aliases[cleaned_name]
        
    for name, role_id in settings.ranks_map.items():
        if name.lower() == cleaned_name:
            role = guild.get_role(role_id)
            if role:
                return role
    for role in guild.roles:
        if role.name.lower() == cleaned_name:
            return role
    return None

def parse_duration(duration_str: str) -> datetime.datetime | None:
    duration_str = duration_str.strip().lower()
    if not duration_str or duration_str in ('навсегда', 'перманентно', 'вечно', 'бессрочно', '-', 'нет'):
        return None
    
    match = re.match(r'^(\d+)\s*([а-яa-z]+)', duration_str)
    if not match:
        return None
        
    val = int(match.group(1))
    unit = match.group(2)
    
    now = datetime.datetime.now()
    if unit.startswith('мин') or unit.startswith('min') or unit == 'м' or unit == 'm':
        return now + datetime.timedelta(minutes=val)
    elif unit.startswith('час') or unit.startswith('hour') or unit == 'ч' or unit == 'h':
        return now + datetime.timedelta(hours=val)
    elif unit.startswith('дн') or unit.startswith('день') or unit.startswith('дня') or unit.startswith('day') or unit == 'д' or unit == 'd':
        return now + datetime.timedelta(days=val)
    elif unit.startswith('нед') or unit.startswith('week') or unit == 'н' or unit == 'w':
        return now + datetime.timedelta(weeks=val)
    elif unit.startswith('мес') or unit.startswith('month'):
        return now + datetime.timedelta(days=val * 30)
    elif unit.startswith('год') or unit.startswith('лет') or unit.startswith('year') or unit == 'г' or unit == 'y':
        return now + datetime.timedelta(days=val * 365)
        
    return None

def v2_msg(text: str) -> disnake.ui.Container:
    text = text.replace("", "").replace("", "").replace("🔒", "").replace("⏳", "").replace("⚠", "").replace("", "").strip()
    return disnake.ui.Container(
        disnake.ui.TextDisplay(text),
        accent_colour=disnake.Colour(0x2C2F33)
    )

def get_cached_val(user_id: int, modal_name: str, field_name: str, default: str = "") -> str:
    entry = MODAL_CACHE.get(user_id)
    if not entry:
        return default
    if time.monotonic() - entry.get("_ts", 0) > _MODAL_CACHE_TTL:
        MODAL_CACHE.pop(user_id, None)
        return default
    return entry.get(modal_name, {}).get(field_name, default)

def set_cached_val(user_id: int, modal_name: str, field_name: str, value: str):
    _cleanup_modal_cache()
    if user_id not in MODAL_CACHE:
        MODAL_CACHE[user_id] = {"_ts": time.monotonic()}
    MODAL_CACHE[user_id]["_ts"] = time.monotonic()
    if modal_name not in MODAL_CACHE[user_id]:
        MODAL_CACHE[user_id][modal_name] = {}
    MODAL_CACHE[user_id][modal_name][field_name] = value

def clean_role_name(name: str) -> str:
    import re
    return re.sub(r'^\[.*?\]\s*', '', name)

def shorten_dept(dept: str) -> str:
    if not dept or dept == "Нет":
        return ""
    d = dept.lower()
    if "спец" in d: return "1СБ"
    if "батальон" in d: return "1 ОБ"
    # if "мто" in d: return "БМТО"  # БМТО больше нет
    if "осб" in d: return "ОСБ"
    if "академия" in d: return "Академия"
    if "цппс" in d: return "ЦППС"
    return dept

def get_nickname_and_roles_for_rank(base_name: str, rank: str, current_dept: str) -> tuple[str, str | None, str | None]:

    rank_lower = rank.lower().strip()
    
    if rank_lower == "рядовой":
        return (f"Курсант 1К | {base_name}", "1-й курс", "Академия ГИБДД")
    elif rank_lower in ("младший сержант", "мл.сержант", "мл. сержант", "мл.сержант полиции"):
        return (f"Курсант 2К | {base_name}", "2-й курс", "Академия ГИБДД")
    
    current_dept = canonical_dept(current_dept)
    if current_dept.lower() in _ACADEMY_DEPTS:
        current_dept = SEPARATE_BATTALION

    dept_short = shorten_dept(current_dept)
    pos_role = None
    nick = f"{base_name}"

    # БМТО больше нет
    # if current_dept in ("МТО", "БМТО"):
    #     nick = f"Инспектор БМТО | {base_name}"
    #     pos_role = "Инспектор группы обеспечения"
    if current_dept == "ОСБ":
        nick = f"Инсп.опер.реагирования | {base_name}"
        pos_role = "Инспектор оперативного реагирования"
    elif current_dept == "ЦППС":
        if rank_lower in ("сержант", "старший сержант", "ст.сержант", "ст. сержант"):
            nick = f"Стажёр ЦППС | {base_name}"
            pos_role = "Стажёр ЦППС"
        elif rank_lower in ("старшина", "прапорщик", "старший прапорщик", "ст. прапорщик"):
            nick = f"Преподаватель ЦППС | {base_name}"
            pos_role = "Преподаватель ЦППС"
        else:
            nick = f"Ст.Преподаватель ЦППС | {base_name}"
            pos_role = "Ст.Преподаватель ЦППС"
    elif current_dept in ("1-й спец бат", "1СБ"):
        if rank_lower in ("сержант", "старший сержант", "ст.сержант", "ст. сержант"):
            nick = f"Стажёр 1СБ | {base_name}"
            pos_role = "Стажёр 1СБ"
        elif rank_lower in ("старший лейтенант", "ст. лейтенант", "капитан"):
            nick = f"Старший Инспектор 1СБ | {base_name}"
            pos_role = "Инспектор 1СБ"
        else:
            nick = f"Инспектор 1СБ | {base_name}"
            pos_role = "Инспектор 1СБ"
    elif current_dept == SEPARATE_BATTALION:
        if rank_lower in ("сержант", "старший сержант", "ст.сержант", "ст. сержант"):
            nick = f"Стажёр {dept_short} | {base_name}"
            pos_role = "Стажёр БП"
        else:
            nick = f"Инспектор {dept_short} | {base_name}"
            pos_role = "Инспектор БП"
    else:
        if rank_lower in ("сержант", "старший сержант", "ст.сержант", "ст. сержант"):
            nick = f"Стажёр {dept_short} | {base_name}"
        else:
            nick = f"Инспектор {dept_short} | {base_name}"
            
    return (nick, pos_role, current_dept)
        
def fit_nickname(prefix: str, base_name: str, max_len: int = 32) -> str:
    full = f"{prefix} | {base_name}"
    if len(full) <= max_len:
        return full
    short_map = {
        "Инсп.опер.реагирования": "Инсп.ОР",
        "Инспектор БМТО": "Инсп.БМТО",
        "Ст.Преподаватель ЦППС": "Ст.Преп.ЦППС",
        "Преподаватель ЦППС": "Преп.ЦППС",
        "Стажёр ЦППС": "Стаж.ЦППС",
        "Старший Инспектор 1СБ": "Ст.Инсп.1СБ",
        "Инспектор 1СБ": "Инсп.1СБ",
        "Стажёр 1СБ": "Стаж.1СБ",
        "Инспектор 1 ОБ": "Инсп.1ОБ",
        "Стажёр 1 ОБ": "Стаж.1ОБ",
        "Инспектор 1БП": "Инсп.1БП",
        "Инспектор 2БП": "Инсп.2БП",
        "Инспектор 3БП": "Инсп.3БП",
        "Стажёр 1БП": "Стаж.1БП",
        "Стажёр 2БП": "Стаж.2БП",
        "Стажёр 3БП": "Стаж.3БП",
        "Курсант 1К": "К.1К",
        "Курсант 2К": "К.2К",
    }
    short_prefix = short_map.get(prefix, prefix)
    full = f"{short_prefix} | {base_name}"
    if len(full) <= max_len:
        return full
    available = max_len - len(short_prefix) - 3
    if available > 0:
        return f"{short_prefix} | {base_name[:available]}"
    return full[:max_len]

STANDARD_PREFIXES = {
    "Инсп.опер.реагирования", "Инсп.ОР",
    "Инспектор БМТО", "Инсп.БМТО",
    "Ст.Преподаватель ЦППС", "Ст.Преп.ЦППС",
    "Преподаватель ЦППС", "Преп.ЦППС",
    "Стажёр ЦППС", "Стаж.ЦППС",
    "Старший Инспектор 1СБ", "Ст.Инсп.1СБ",
    "Инспектор 1СБ", "Инсп.1СБ",
    "Стажёр 1СБ", "Стаж.1СБ",
    "Инспектор 1 ОБ", "Инсп.1ОБ",
    "Стажёр 1 ОБ", "Стаж.1ОБ",
    "Инспектор 1БП", "Инсп.1БП",
    "Инспектор 2БП", "Инсп.2БП",
    "Инспектор 3БП", "Инсп.3БП",
    "Стажёр БП", "Инспектор БП",
    "Стажёр 1БП", "Стаж.1БП",
    "Стажёр 2БП", "Стаж.2БП",
    "Стажёр 3БП", "Стаж.3БП",
    "Курсант 1К", "К.1К",
    "Курсант 2К", "К.2К",
    "Уволен",
    "Гражданин"
}

async def sync_user_roles_and_nickname(target: disnake.Member, guild: disnake.Guild, rank: str, bot_member: disnake.Member, override_dept: str = None, nickname_override: str = None) -> tuple[list[str], list[str], list[str]]:
    issued = []
    removed = []
    errors = []

    def _add_safe(role):
        if role and role not in target.roles:
            if can_manage_role(bot_member, role):
                return True
            errors.append(f"Роль '{role.name}' выше бота")
        return False
        
    def _rem_safe(role):
        if role and role in target.roles:
            if can_manage_role(bot_member, role):
                return True
            errors.append(f"Роль '{role.name}' выше бота")
        return False

    base = guild.get_role(settings.base_role_id)
    if _add_safe(base):
        try:
            await target.add_roles(base)
            issued.append(clean_role_name(base.name))
        except Exception as e: errors.append(str(e))

    div_ids = []
    if settings.divider_position_id: div_ids.append(settings.divider_position_id)
    if settings.divider_department_id: div_ids.append(settings.divider_department_id)
    if settings.divider_rank_id: div_ids.append(settings.divider_rank_id)
    if settings.divider_access_id: div_ids.append(settings.divider_access_id)
    for d_id in div_ids:
        if d_id:
            d_role = guild.get_role(d_id)
            if _add_safe(d_role):
                try:
                    await target.add_roles(d_role)
                    issued.append(clean_role_name(d_role.name))
                except Exception as e: errors.append(str(e))

    rank_lower = rank.lower().strip()
    rank_role = find_rank_role(guild, rank)
    
    for r_name, r_id in settings.ranks_map.items():
        if r_name.lower().strip() != rank_lower:
            old_r = guild.get_role(r_id)
            if _rem_safe(old_r):
                try:
                    await target.remove_roles(old_r)
                    removed.append(clean_role_name(old_r.name))
                except Exception as e: pass

    if _add_safe(rank_role):
        try:
            await target.add_roles(rank_role)
            issued.append(clean_role_name(rank_role.name))
        except Exception as e: errors.append(str(e))
        
    cadet = guild.get_role(settings.cadet_role_id)
    if rank_lower in ("рядовой", "мл. сержант", "младший сержант", "мл.сержант"):
        if _add_safe(cadet):
            try:
                await target.add_roles(cadet)
                issued.append(clean_role_name(cadet.name))
            except Exception as e: errors.append(str(e))
    else:
        if _rem_safe(cadet):
            try:
                await target.remove_roles(cadet)
                removed.append(clean_role_name(cadet.name))
            except Exception as e: pass

    if override_dept:
        current_dept = canonical_dept(override_dept)
    else:
        current_dept = member_department(target, guild)
            
    if nickname_override:
        base_name = nickname_override
    else:
        from database import get_user as _get_user
        _udb = _get_user(target.id)
        if _udb and _udb["nickname"] and _udb["nickname"] not in ("Не указан", ""):
            base_name = _udb["nickname"]
        else:
            base_name = target.display_name
            
    if " | " in base_name:
        base_name = base_name.rsplit(" | ", 1)[-1].strip()
        
    new_nick, new_pos_role_name, new_dept = get_nickname_and_roles_for_rank(base_name, rank, current_dept)
    
    if new_dept and new_dept != "Нет":
        new_role_id = resolve_department_role_id(new_dept)
        role_ids_to_strip = set(settings.department_role_ids.values()) | set(settings.obsolete_department_role_ids)
        for old_role_id in role_ids_to_strip:
            if not old_role_id or old_role_id == new_role_id:
                continue
            old_d = guild.get_role(old_role_id)
            if _rem_safe(old_d):
                try:
                    await target.remove_roles(old_d)
                    removed.append(clean_role_name(old_d.name))
                except Exception: pass
        new_d = guild.get_role(new_role_id) if new_role_id else None
        if _add_safe(new_d):
            try:
                await target.add_roles(new_d)
                issued.append(clean_role_name(new_d.name))
            except Exception: pass
            
    if new_pos_role_name and new_pos_role_name in settings.position_role_ids:
        new_pos_role_id = settings.position_role_ids[new_pos_role_name]
    else:
        new_pos_role_id = None
        
    for pos_name, pos_id in settings.position_role_ids.items():
        pos_r = guild.get_role(pos_id)
        if not pos_r: continue
        
        if pos_id == new_pos_role_id:
            if _add_safe(pos_r):
                try:
                    await target.add_roles(pos_r)
                    issued.append(clean_role_name(pos_r.name))
                except Exception: pass
        else:
            if _rem_safe(pos_r):
                try:
                    await target.remove_roles(pos_r)
                    removed.append(clean_role_name(pos_r.name))
                except Exception: pass

    fired = guild.get_role(settings.fired_role_id)
    if _rem_safe(fired):
        try:
            await target.remove_roles(fired)
            removed.append(clean_role_name(fired.name))
        except Exception: pass
        
    prefix = new_nick.split(" | ", 1)[0] if " | " in new_nick else new_nick
    final_nick = fit_nickname(prefix, base_name) if " | " in new_nick else new_nick[:32]
    
    should_change_nick = True
    current_nick = target.display_name
    if " | " in current_nick:
        current_prefix = current_nick.split(" | ", 1)[0].strip()
        if current_prefix not in STANDARD_PREFIXES:
            should_change_nick = False

    if should_change_nick:
        try:
            await target.edit(nick=final_nick)
        except Exception as e:
            errors.append(f"Ник: {e}")

    return issued, removed, errors
