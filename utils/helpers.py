import re
import time
import datetime
import logging
from urllib.parse import urlparse

import disnake

from config.settings import settings, MIN_MANAGE_RANK, SERGEANT_RANK

logger = logging.getLogger("bot.helpers")


_MODAL_CACHE_TTL = 1800
_MODAL_CACHE_MAX_SIZE = 1000

MODAL_CACHE: dict[int, dict] = {}


def _cleanup_modal_cache() -> None:
    """Удаляет записи из кэша, которые старше TTL."""
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
    """Проверяет, что URL является безопасной HTTP(S) ссылкой.

    Возвращает True если URL валиден, False если подозрителен.
    """
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
    if not settings.staff_role_id:
        return False
    staff_role = guild.get_role(settings.staff_role_id)
    if not staff_role:
        return False
    return staff_role in member.roles


def get_member_rank_index(member: disnake.Member, guild: disnake.Guild) -> int:
    ranks = settings.ranks
    for idx in range(len(ranks) - 1, -1, -1):
        rank_name = ranks[idx]
        role_id = settings.ranks_map.get(rank_name)
        if role_id:
            role = guild.get_role(role_id)
            if role and role in member.roles:
                return idx
        else:
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
        return True
    return get_member_rank_index(member, guild) >= captain_idx


def is_ss(member: disnake.Member) -> bool:
    return any(r.id == 1500573332091703518 for r in member.roles)


def can_manage_applications(member: disnake.Member) -> bool:
    if is_ss(member):
        return True
    
    cpps_roles = ["преподаватель", "цппс", "начальник цппс", "зам. начальника цппс", "заместитель начальника цппс"]
    for role in member.roles:
        name_lower = role.name.lower()
        if any(c in name_lower for c in cpps_roles) and ("преподаватель" in name_lower or "цппс" in name_lower):
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


def find_rank_role(guild: disnake.Guild, rank_name: str) -> disnake.Role | None:
    cleaned_name = rank_name.strip().lower()
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
