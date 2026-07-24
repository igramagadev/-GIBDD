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
    role_id = settings.staff_role_id or settings.ss_role_id
    if not role_id:
        return False
    staff_role = guild.get_role(role_id)
    if not staff_role:
        return False
    return staff_role in member.roles


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
        return True
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


def shorten_dept(dept: str) -> str:
    if not dept or dept == "Нет":
        return ""
    d = dept.lower()
    if "1-й батальон" in d: return "1БП"
    if "2-й батальон" in d: return "2БП"
    if "3-й батальон" in d: return "3БП"
    if "спец" in d: return "1СБ"
    if "мто" in d: return "БМТО"
    if "осб" in d: return "ОСБ"
    if "академия" in d: return "Академия"
    if "цппс" in d: return "ЦППС"
    return dept


def get_nickname_and_roles_for_rank(base_name: str, rank: str, current_dept: str) -> tuple[str, str | None, str | None]:

    rank_lower = rank.lower().strip()
    
    if rank_lower == "рядовой":
        return (f"Курсант 1К | {base_name}", None, None)
    elif rank_lower in ("младший сержант", "мл.сержант", "мл. сержант", "мл.сержант полиции"):
        return (f"Курсант 2К | {base_name}", None, None)
    
    if rank_lower == "сержант":
        if not current_dept or current_dept == "Нет":
            import random
            current_dept = random.choice(["1-й батальон 1 полка", "2-й батальон 1 полка", "3-й батальон 1 полка"])
            
    dept_short = shorten_dept(current_dept)
    pos_role = None
    nick = f"{base_name}"

    if current_dept in ("МТО", "БМТО"):
        nick = f"Инспектор БМТО | {base_name}"
        pos_role = "Инспектор группы обеспечения"
    elif current_dept == "ОСБ":
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
    elif current_dept in ("1-й батальон 1 полка", "2-й батальон 1 полка", "3-й батальон 1 полка"):
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
        
async def sync_user_roles_and_nickname(target: disnake.Member, guild: disnake.Guild, rank: str, bot_member: disnake.Member, override_dept: str = None) -> tuple[list[str], list[str], list[str]]:
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

    div_ids = [settings.divider_position_id, settings.divider_department_id, settings.divider_rank_id]
    if settings.ss_role_id and is_ss(target):
        div_ids.append(settings.divider_access_id)
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

    current_dept = "Нет"
    if override_dept:
        current_dept = override_dept
    else:
        for dept_name, role_id in settings.department_role_ids.items():
            r = guild.get_role(role_id)
            if r and r in target.roles:
                current_dept = dept_name
                break
            
    base_name = target.display_name
    if " | " in base_name:
        base_name = base_name.split(" | ", 1)[1]
    elif "] " in base_name:
        base_name = base_name.split("] ", 1)[1]
        
    new_nick, new_pos_role_name, new_dept = get_nickname_and_roles_for_rank(base_name, rank, current_dept)
    
    if new_dept and new_dept != "Нет":
        for old_dept_name, old_role_id in settings.department_role_ids.items():
            if old_dept_name != new_dept:
                old_d = guild.get_role(old_role_id)
                if _rem_safe(old_d):
                    try:
                        await target.remove_roles(old_d)
                        removed.append(clean_role_name(old_d.name))
                    except Exception: pass
        new_d = guild.get_role(settings.department_role_ids.get(new_dept))
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
        
    try:
        await target.edit(nick=new_nick[:32])
    except Exception as e:
        errors.append(f"Ник: {e}")

    return issued, removed, errors
