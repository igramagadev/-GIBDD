import logging
import time
from datetime import datetime
import disnake
from disnake.ext import commands
from config.settings import settings
from database import (
    add_audit_record,
    get_user,
    add_or_update_user,
    set_user_status,
    is_blacklisted,
)
from utils.helpers import (
    clean_role_name,
    can_manage_role,
    can_manage_audit,
    find_rank_role,
    send_dm,
    v2_msg,
    get_staff_title,
    get_member_rank_index,
    is_rank_sergeant_or_above,
    list_departments,
    member_department,
    separate_battalion_pings,
    employee_refusal,
    promotion_cooldown_notice,
)
from utils.panel_init import send_v2_panel
logger = logging.getLogger("bot.audit")
AUDIT_SESSIONS: dict[tuple[int, int], dict] = {}
_AUDIT_SESSION_TTL = 600
def _set_audit_session(user_id: int, guild_id: int, data: dict) -> None:
    _cleanup_audit_sessions()
    data["_ts"] = time.monotonic()
    AUDIT_SESSIONS[(user_id, guild_id)] = data
def _get_audit_session(user_id: int, guild_id: int) -> dict | None:
    key = (user_id, guild_id)
    session = AUDIT_SESSIONS.get(key)
    if not session:
        return None
    if time.monotonic() - session.get("_ts", 0) > _AUDIT_SESSION_TTL:
        AUDIT_SESSIONS.pop(key, None)
        return None
    return session
def _cleanup_audit_sessions() -> None:
    now = time.monotonic()
    expired = [
        key for key, data in AUDIT_SESSIONS.items()
        if now - data.get("_ts", 0) > _AUDIT_SESSION_TTL
    ]
    for key in expired:
        del AUDIT_SESSIONS[key]
async def post_audit_container(guild, container):
    channel = guild.get_channel(settings.audit_log_channel_id)
    if channel is None and settings.audit_log_channel_id:
        try:
            channel = await guild.fetch_channel(settings.audit_log_channel_id)
        except Exception:
            channel = None
    if not channel:
        logger.warning("Канал аудита %s не найден", settings.audit_log_channel_id)
        return False
    try:
        await channel.send(components=[container])
        return True
    except disnake.HTTPException as exc:
        logger.error("Ошибка отправки контейнера аудита: %s", exc)
        return False
def build_audit_container(action_verb, performer, target, static_id,
                          old_rank=None, new_rank=None, reason=None,
                          issued_roles=None, removed_roles=None,
                          old_department=None, new_department=None):
    action_title_map = {
        "принимает": "Принятие на службу",
        "увольняет": "Увольнение со службы",
        "понижает": "Понижение в звании",
        "повышает": "Повышение в звании",
        "переводит": "Перевод по отделам",
    }
    action_title = action_title_map.get(action_verb.lower(), "Действие кадрового аудита")
    desc = f"### Журнал — {action_title}\n"
    desc += "*Единая запись кадрового аудита*\n\n"
    lines = []
    lines.append(f"**Действие**: {action_title}")
    lines.append(f"**Исполнитель**: {performer.mention} ({performer.id})")
    target_val = f"{target.mention} ({target.id})"
    if static_id:
        target_val += f" | Static ID: `{static_id}`"
    lines.append(f"**Сотрудник**: {target_val}")
    if old_rank and new_rank:
        lines.append(f"**Было**: {old_rank}")
        lines.append(f"**Стало**: {new_rank}")
    elif new_rank:
        lines.append(f"**Звание**: {new_rank}")
    if old_department and new_department:
        lines.append(f"**Из отдела**: {old_department}")
        lines.append(f"**В отдел**: {new_department}")
    if removed_roles:
        lines.append(f"**Снятые роли**: {removed_roles}")
    if issued_roles:
        lines.append(f"**Выданные роли**: {issued_roles}")
    if reason:
        lines.append(f"**Причина/Рапорт**: {reason}")
    desc += "\n".join(f"> {line}" for line in lines)
    timestamp = int(datetime.now().timestamp())
    footer_text = f"Время: <t:{timestamp}:F> (<t:{timestamp}:R>)"
    return disnake.ui.Container(
        disnake.ui.TextDisplay(desc),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(footer_text),
        accent_colour=disnake.Colour(0x2C2F33)
    )
class AuditAcceptUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.user_select(placeholder="Выберите пользователя...", custom_id="audit:select_user_accept")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        target = select.values[0]
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message(components=[v2_msg("Недостаточно прав. ")], ephemeral=True)
            return
        if interaction.user.id == target.id:
            await interaction.response.send_message(components=[v2_msg("Нельзя принимать самого себя.")], ephemeral=True)
            return
        _set_audit_session(interaction.user.id, interaction.guild.id, {
            "target_id": target.id,
            "action": "Accept"
        })
        await interaction.response.send_modal(AuditAcceptModal())
class AuditAcceptModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Static ID",
                custom_id="static_id",
                required=True,
                max_length=50
            ),
            disnake.ui.TextInput(
                label="Способ принятия",
                custom_id="method",
                placeholder="Например: Собеседование",
                required=True,
                max_length=50
            ),
            disnake.ui.TextInput(
                label="Звание",
                custom_id="rank",
                placeholder="Например: Рядовой",
                required=True,
                max_length=50
            ),
            disnake.ui.TextInput(
                label="Комментарий (необязательно)",
                custom_id="reason",
                required=False,
                max_length=100
            )
        ]
        super().__init__(title="Принятие сотрудника", components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        performer = interaction.user
        guild = interaction.guild
        static_id_val = interaction.text_values["static_id"].strip()
        method_val = interaction.text_values["method"].strip()
        rank_val = interaction.text_values["rank"].strip()
        reason_val = interaction.text_values.get("reason", "").strip()
        session = _get_audit_session(performer.id, guild.id)
        if not session:
            await interaction.followup.send(components=[v2_msg("Сессия истекла.")], ephemeral=True)
            return
        target_id = session["target_id"]
        target = guild.get_member(target_id)
        if not target:
            await interaction.followup.send(components=[v2_msg("Сотрудник не найден.")], ephemeral=True)
            return
        if is_blacklisted(target_id):
            await interaction.followup.send(
                components=[v2_msg("Пользователь в Чёрном Списке (ЧС)! Принятие заблокировано.")],
                ephemeral=True
            )
            return
        user_db = get_user(target_id)
        if user_db and user_db["status"] == "active":
            await interaction.followup.send(
                components=[v2_msg("Данный сотрудник уже трудоустроен!")],
                ephemeral=True
            )
            return
        bot_member = guild.get_member(interaction.client.user.id)
        from utils.helpers import sync_user_roles_and_nickname
        issued_roles, removed_roles, errors = await sync_user_roles_and_nickname(target, guild, rank_val, bot_member)
        add_audit_record(
            action="Принять",
            target_user_id=target.id,
            target_user_name=str(target),
            target_static_id=static_id_val,
            target_rank=rank_val,
            target_position="",
            method=method_val,
            reason=reason_val,
            performed_by_id=performer.id,
            performed_by_name=str(performer),
            issued_roles=", ".join(issued_roles) if issued_roles else "Нет",
            removed_roles="Нет"
        )
        add_or_update_user(target.id, target.display_name, static_id_val, rank_val, "active")
        audit_reason = method_val
        if reason_val:
            audit_reason += f" ({reason_val})"
        await post_audit_container(
            guild,
            build_audit_container(
                "принимает", performer, target, static_id_val,
                new_rank=rank_val, reason=audit_reason,
                issued_roles=", ".join(issued_roles) if issued_roles else None
            )
        )
        staff_title = get_staff_title(performer, guild)
        desc_dm = (
            f"### Уведомление о принятии на службу\n\n"
            f"Вы были **приняты на службу** в УГИБДД {staff_title}.\n"
            f"> **Static ID:** {static_id_val}\n"
            f"> **Звание:** {rank_val}\n"
            f"> **Способ принятия:** {method_val}\n"
            f"> **Выданные роли:** {', '.join(issued_roles) if issued_roles else 'Нет'}"
        )
        dm_container = disnake.ui.Container(
            disnake.ui.TextDisplay(desc_dm),
            accent_colour=disnake.Colour(0x2C2F33)
        )
        dm_status = "ЛС отправлены" if await send_dm(target, components=[dm_container]) else "ЛС закрыты"
        import asyncio
        from utils.roster_generator import update_cpps_roster
        asyncio.create_task(update_cpps_roster(guild))
        response = f"{target.mention} принят!"
        if issued_roles:
            response += f"\nРоли: {', '.join(issued_roles)}"
        if errors:
            response += f"\nОшибки: {', '.join(errors)}"
        response += f"\n{dm_status}"
        await interaction.followup.send(components=[v2_msg(response)], ephemeral=True)
class AuditDismissUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.user_select(placeholder="Выберите сотрудника...", custom_id="audit:select_user_dismiss")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        target = select.values[0]
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message(components=[v2_msg("Недостаточно прав. ")], ephemeral=True)
            return
        if interaction.user.id == target.id:
            await interaction.response.send_message(components=[v2_msg("Нельзя уволить самого себя.")], ephemeral=True)
            return
            
        user_db = get_user(target.id)
        if user_db and user_db.get("status") == "fired":
            await interaction.response.send_message(components=[v2_msg("Этот сотрудник уже уволен.")], ephemeral=True)
            return
            
        if not any(r.id == settings.base_role_id for r in target.roles):
            await interaction.response.send_message(components=[v2_msg("Пользователь не является сотрудником (отсутствует базовая роль).")], ephemeral=True)
            return
        _set_audit_session(interaction.user.id, interaction.guild.id, {
            "target_id": target.id,
            "action": "Dismiss"
        })
        await interaction.response.send_modal(AuditDismissReasonModal())
class AuditDismissReasonModal(disnake.ui.Modal):
    def __init__(self, needs_static: bool = False):
        components = []
        if needs_static:
            components.append(disnake.ui.TextInput(
                label="Static ID",
                custom_id="static_id",
                placeholder="Например: 111-111",
                required=True,
                max_length=20
            ))
        components.append(disnake.ui.TextInput(
            label="Причина / Рапорт",
            custom_id="reason",
            required=True,
            max_length=500,
            style=disnake.TextInputStyle.paragraph
        ))
        components.append(disnake.ui.TextInput(
            label="Занести в ЧС? (Да / Нет)",
            custom_id="bl_decision",
            required=True,
            max_length=10
        ))
        components.append(disnake.ui.TextInput(
            label="Срок ЧС (если Да)",
            custom_id="bl_duration",
            required=False,
            placeholder="Например: 15 дней, 2 месяца, навсегда",
            max_length=50
        ))
        super().__init__(title="Увольнение сотрудника", components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        performer = interaction.user
        guild = interaction.guild
        reason_val = interaction.text_values["reason"].strip()
        session = _get_audit_session(performer.id, guild.id)
        if not session:
            await interaction.followup.send(components=[v2_msg("Сессия истекла.")], ephemeral=True)
            return
        target_id = session["target_id"]
        target = guild.get_member(target_id)
        if not target:
            await interaction.followup.send(components=[v2_msg("Сотрудник не найден.")], ephemeral=True)
            return
        user_db = get_user(target_id)
        static_id_input = interaction.text_values.get("static_id")
        if static_id_input and user_db:
            static_id = static_id_input.strip()
            add_or_update_user(target_id, user_db["nickname"], static_id, user_db["rank"], user_db["status"])
            session["static_id"] = static_id
        else:
            static_id = user_db["static_id"] if user_db else "Не указан"
        bot_member = guild.get_member(interaction.client.user.id)
        errors = []
        removed_roles_list = []
        cleanup_ids = settings.roles_to_cleanup_ids
        cleanup_names = settings.roles_to_cleanup_names
        extra_cleanup_ids = set()
        if settings.divider_position_id: extra_cleanup_ids.add(settings.divider_position_id)
        if settings.divider_department_id: extra_cleanup_ids.add(settings.divider_department_id)
        if settings.divider_rank_id: extra_cleanup_ids.add(settings.divider_rank_id)
        if settings.divider_access_id: extra_cleanup_ids.add(settings.divider_access_id)
        extra_cleanup_ids.update(settings.department_role_ids.values())
        extra_cleanup_ids.update(settings.obsolete_department_role_ids)
        for role in target.roles:
            is_cleanup = False
            if cleanup_ids and role.id in cleanup_ids:
                is_cleanup = True
            elif role.name in cleanup_names:
                is_cleanup = True
            elif role.id in extra_cleanup_ids:
                is_cleanup = True
            elif role.id in (settings.base_role_id, settings.cadet_role_id):
                is_cleanup = True
            if role.id in settings.ranks_map.values():
                is_cleanup = True
            if is_cleanup and can_manage_role(bot_member, role):
                try:
                    await target.remove_roles(role)
                    removed_roles_list.append(clean_role_name(role.name))
                except Exception as exc:
                    errors.append(f"{role.name}: {exc}")
        fired_role = guild.get_role(settings.fired_role_id)
        issued_roles_list = []
        if fired_role and fired_role not in target.roles and can_manage_role(bot_member, fired_role):
            try:
                await target.add_roles(fired_role)
                issued_roles_list.append(clean_role_name(fired_role.name))
            except Exception as exc:
                errors.append(f"Уволен: {exc}")
        if user_db and user_db["nickname"] and user_db["nickname"] not in ("Не указан", ""):
            base_name = user_db["nickname"]
        else:
            base_name = target.display_name
            
        if " | " in base_name:
            base_name = base_name.split(" | ")[-1]
            
        fired_nick = f"Уволен | {base_name}"
        if len(fired_nick) > 32:
            available = 32 - len("Уволен | ")
            fired_nick = f"Уволен | {base_name[:available]}"
        try:
            await target.edit(nick=fired_nick)
        except Exception as exc:
            errors.append(f"Ошибка изменения ника: {exc}")
        add_audit_record(
            action="Уволить",
            target_user_id=target.id,
            target_user_name=str(target),
            target_static_id=static_id,
            target_rank="",
            target_position="",
            method="",
            reason=reason_val,
            performed_by_id=performer.id,
            performed_by_name=str(performer),
            issued_roles=", ".join(issued_roles_list) if issued_roles_list else "Нет",
            removed_roles=", ".join(removed_roles_list) if removed_roles_list else "Нет"
        )
        set_user_status(target.id, "fired")

        bl_decision = interaction.text_values.get("bl_decision", "").strip().lower()
        want_bl = bl_decision in ("да", "yes", "д", "y", "+")
        if want_bl:
            from utils.helpers import give_blacklist
            bl_duration = interaction.text_values.get("bl_duration", "").strip()
            errors.extend(await give_blacklist(
                guild,
                target,
                user_id=target.id,
                nickname=base_name,
                static_id=static_id,
                reason=reason_val,
                duration_str=bl_duration,
                added_by=performer,
            ))
        await post_audit_container(
            guild,
            build_audit_container(
                "увольняет", performer, target, static_id,
                reason=reason_val,
                removed_roles=", ".join(removed_roles_list) if removed_roles_list else None
            )
        )
        staff_title = get_staff_title(performer, guild)
        desc_dm = (
            f"### Уведомление об увольнении\n\n"
            f"Вы были **уволены со службы** {staff_title}.\n"
            f"> **Причина:** {reason_val}"
        )
        dm_container = disnake.ui.Container(
            disnake.ui.TextDisplay(desc_dm),
            accent_colour=disnake.Colour(0x2C2F33)
        )
        dm_status = "ЛС отправлены" if await send_dm(target, components=[dm_container]) else "ЛС закрыты"
        import asyncio
        from utils.roster_generator import update_cpps_roster
        asyncio.create_task(update_cpps_roster(guild))
        response = f"{target.mention} уволен!\n"
        if removed_roles_list:
            response += f"Снято: {', '.join(removed_roles_list)}\n"
        if errors:
            response += f"Ошибки: {', '.join(errors)}\n"
        response += dm_status
        await interaction.followup.send(
            components=[v2_msg(response)],
            ephemeral=True
        )
class AuditSelectRankView(disnake.ui.View):
    def __init__(self, action: str, valid_ranks: list[str], needs_static: bool):
        super().__init__(timeout=None)
        self.action = action
        self.needs_static = needs_static
        options = [disnake.SelectOption(label=rank, value=rank) for rank in valid_ranks]
        self.select_rank = disnake.ui.StringSelect(
            placeholder="Выберите звание...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"audit:select_rank_{action}"
        )
        self.select_rank.callback = self.rank_callback
        self.add_item(self.select_rank)
    async def rank_callback(self, interaction: disnake.MessageInteraction):
        new_rank = self.select_rank.values[0]
        session = _get_audit_session(interaction.user.id, interaction.guild.id)
        if session:
            session["new_rank"] = new_rank
        await interaction.response.send_modal(AuditPromoteDemoteReasonModal(self.action, self.needs_static))
class AuditPromoteDemoteReasonModal(disnake.ui.Modal):
    def __init__(self, action: str, needs_static: bool = False):
        self.action = action
        title = "Повышение" if action == "Promote" else "Понижение"
        components = []
        if needs_static:
            components.append(disnake.ui.TextInput(
                label="Static ID",
                custom_id="static_id",
                placeholder="Пример: 123456",
                required=True,
                max_length=20
            ))
        components.append(disnake.ui.TextInput(
            label="Причина / Рапорт",
            custom_id="reason",
            required=True,
            max_length=500,
            style=disnake.TextInputStyle.paragraph
        ))
        super().__init__(title=title, components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        performer = interaction.user
        guild = interaction.guild
        reason_val = interaction.text_values["reason"].strip()
        session = _get_audit_session(performer.id, guild.id)
        if not session:
            await interaction.followup.send(
                components=[v2_msg("Сессия истекла или не найдена. Начните выбор заново.")],
                ephemeral=True
            )
            return
        target_id = session["target_id"]
        new_rank = session.get("new_rank", "")
        static_id_input = interaction.text_values.get("static_id")
        if static_id_input:
            session["static_id"] = static_id_input.strip()
            user_db = get_user(target_id)
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], session["static_id"], user_db["rank"], user_db["status"])
        action = session["action"]
        static_id = session["static_id"]
        old_rank = session["old_rank"]
        target = guild.get_member(target_id)
        if not target:
            await interaction.followup.send(components=[v2_msg("Сотрудник не найден.")], ephemeral=True)
            return
        bot_member = guild.get_member(interaction.client.user.id)
        from utils.helpers import sync_user_roles_and_nickname
        issued_roles, removed_roles_list, errors = await sync_user_roles_and_nickname(target, guild, new_rank, bot_member)
        action_verb = "повышает" if action == "Promote" else "понижает"
        audit_action = "Повысить" if action == "Promote" else "Понизить"
        add_audit_record(
            action=audit_action,
            target_user_id=target.id,
            target_user_name=str(target),
            target_static_id=static_id,
            target_rank=new_rank,
            target_position="",
            method="",
            reason=f"С {old_rank} на {new_rank}. {reason_val}",
            performed_by_id=performer.id,
            performed_by_name=str(performer),
            issued_roles=", ".join(issued_roles) if issued_roles else "Нет",
            removed_roles=", ".join(removed_roles_list) if removed_roles_list else "Нет",
        )
        user_db = get_user(target.id)
        if user_db:
            add_or_update_user(target.id, user_db["nickname"], user_db["static_id"], new_rank, "active")
        await post_audit_container(
            guild,
            build_audit_container(
                action_verb, performer, target, static_id,
                old_rank=old_rank, new_rank=new_rank, reason=reason_val,
                issued_roles=", ".join(issued_roles) if issued_roles else None,
                removed_roles=", ".join(removed_roles_list) if removed_roles_list else None,
            ),
        )
        logger.info(
            "КАДРОВЫЙ АУДИТ | %s | Сотрудник: %s (ID: %s) | Static ID: %s | С %s на %s | Причина: %s | Снял: %s | Выдал: %s | Выполнил: %s (ID: %s)",
            audit_action.upper(), target, target.id, static_id, old_rank, new_rank, reason_val,
            ", ".join(removed_roles_list), ", ".join(issued_roles), performer, performer.id
        )
        import asyncio
        from utils.roster_generator import update_cpps_roster
        asyncio.create_task(update_cpps_roster(guild))
        staff_title = get_staff_title(performer, guild)
        action_word = "повышен" if action == "Promote" else "понижен"
        desc_dm = (
            f"### Уведомление об изменении звания\n\n"
            f"Вы были **{action_word}** {staff_title}.\n"
            f"> **Было:** {old_rank}\n"
            f"> **Стало:** {new_rank}\n"
            f"> **Причина:** {reason_val}"
        )
        dm_container = disnake.ui.Container(
            disnake.ui.TextDisplay(desc_dm),
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await send_dm(target, components=[dm_container])
        if action == "Promote" and new_rank.lower().strip() in ("сержант", "сержант полиции"):
            assigned_dept = member_department(target, guild)
            pings = separate_battalion_pings(assigned_dept)
            if pings:
                assign_text = (
                    f"{pings}\n"
                    f"**Зачисление в 1-й Отдельный Батальон**\n\n"
                    f"**Инициатор:** {performer.mention}\n"
                    f"**Сотрудник:** {target.mention}\n"
                    f"**Статик:** {static_id}\n"
                    f"**Отдел:** {assigned_dept}"
                )
                try:
                    if settings.battalion_assignment_channel_id:
                        ch = guild.get_channel(settings.battalion_assignment_channel_id)
                        if ch is None:
                            ch = await guild.fetch_channel(settings.battalion_assignment_channel_id)
                        if ch:
                            from utils.helpers import send_notice
                            await send_notice(ch, assign_text)
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление о зачислении: {e}")

            from utils.roster_generator import update_cpps_roster
            import asyncio
            asyncio.create_task(update_cpps_roster(interaction.guild))

        response = f"{target.mention}: {old_rank} → {new_rank}"
        if errors:
            response += f"\nОшибки: {', '.join(errors)}"
        await interaction.followup.send(components=[v2_msg(response)], ephemeral=True)

class AuditDemoteUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.user_select(placeholder="Выберите сотрудника...", custom_id="audit:select_user_demote")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        target = select.values[0]
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message(components=[v2_msg("Недостаточно прав. ")], ephemeral=True)
            return
        if interaction.user.id == target.id:
            await interaction.response.send_message(components=[v2_msg("Нельзя понижать самого себя.")], ephemeral=True)
            return
        user_db = get_user(target.id)
        if not user_db:
            await interaction.response.send_message(components=[v2_msg("Пользователь не найден в базе данных.")], ephemeral=True)
            return
            
        if user_db.get("status") == "fired":
            await interaction.response.send_message(components=[v2_msg("Этот сотрудник уволен. Дальнейшие кадровые действия невозможны.")], ephemeral=True)
            return
            
        if not any(r.id == settings.base_role_id for r in target.roles):
            await interaction.response.send_message(components=[v2_msg("Пользователь не является сотрудником (отсутствует базовая роль).")], ephemeral=True)
            return
        
        current_rank = user_db.get("rank", "Не указано") or "Не указано"
        static_id = user_db.get("static_id", "Не указан") or "Не указан"
        nickname = user_db.get("nickname", target.display_name) or target.display_name
        
        idx = -1
        if current_rank in settings.ranks:
            idx = settings.ranks.index(current_rank)
        if idx <= 0:
            await interaction.response.send_message(components=[v2_msg(f"Невозможно понизить сотрудника со званием '{current_rank}'.")], ephemeral=True)
            return
        valid_ranks = settings.ranks[:idx]
        needs_static = static_id in ("Не указан", None, "")
        
        _set_audit_session(interaction.user.id, interaction.guild.id, {
            "target_id": target.id,
            "action": "Demote",
            "old_rank": current_rank,
            "static_id": static_id,
            "valid_ranks": valid_ranks,
            "needs_static": needs_static,
        })
        
        joined_str = "—"
        if hasattr(target, "joined_at") and target.joined_at:
            joined_str = f"<t:{int(target.joined_at.timestamp())}:D>"

        info_text = (
            f"### Информация о сотруднике\n\n"
            f"**Сотрудник:** {target.mention} (`{target}`)\n"
            f"**Никнейм:** {nickname}\n"
            f"**Статик:** `{static_id}`\n"
            f"**Текущее звание:** {current_rank}\n"
            f"**На сервере с:** {joined_str}\n\n"
            f"Вы уверены, что хотите **понизить** этого сотрудника?"
        )

        confirm_view = AuditDemoteConfirmView(valid_ranks=valid_ranks, needs_static=needs_static)
        action_row = disnake.ui.ActionRow(*confirm_view.children)
        card = disnake.ui.Container(
            disnake.ui.TextDisplay(info_text),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await interaction.response.send_message(components=[card], ephemeral=True)
        msg = await interaction.original_message()
        interaction.bot._connection.store_view(confirm_view, msg.id)

class AuditDemoteConfirmView(disnake.ui.View):
    def __init__(self, valid_ranks: list[str], needs_static: bool):
        super().__init__(timeout=300)
        self.valid_ranks = valid_ranks
        self.needs_static = needs_static

    @disnake.ui.button(label="Продолжить", style=disnake.ButtonStyle.success, custom_id="audit:demote_confirm")
    async def confirm(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        session = _get_audit_session(interaction.user.id, interaction.guild.id)
        if not session:
            await interaction.response.send_message(components=[v2_msg("Сессия истекла. Начните выбор заново.")], ephemeral=True)
            return
        valid_ranks = session.get("valid_ranks", self.valid_ranks)
        needs_static = session.get("needs_static", self.needs_static)
        rank_view = AuditSelectRankView("Demote", valid_ranks, needs_static)
        action_row = disnake.ui.ActionRow(*rank_view.children)
        card = disnake.ui.Container(
            disnake.ui.TextDisplay("Выберите звание для понижения:"),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await interaction.response.edit_message(components=[card])
        interaction.bot._connection.store_view(rank_view, interaction.message.id)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.danger, custom_id="audit:demote_cancel")
    async def cancel(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.edit_message(
            components=[disnake.ui.Container(
                disnake.ui.TextDisplay("Действие отменено."),
                accent_colour=disnake.Colour(0x2C2F33)
            )]
        )
class AuditPromoteUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.user_select(placeholder="Выберите сотрудника...", custom_id="audit:select_user_promote")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        target = select.values[0]
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message(components=[v2_msg("Недостаточно прав. ")], ephemeral=True)
            return
        if interaction.user.id == target.id:
            await interaction.response.send_message(components=[v2_msg("Нельзя повышать самого себя.")], ephemeral=True)
            return
        user_db = get_user(target.id)
        refusal = employee_refusal(target, user_db, self_target=False)
        if refusal:
            await interaction.response.send_message(components=[v2_msg(refusal)], ephemeral=True)
            return
        
        from database import get_last_promotion_time
        
        current_rank = user_db.get("rank", "Не указано") or "Не указано"
        static_id = user_db.get("static_id", "Не указан") or "Не указан"
        nickname = user_db.get("nickname", target.display_name) or target.display_name

        target_idx = -1
        if current_rank in settings.ranks:
            target_idx = settings.ranks.index(current_rank)
        performer_rank_idx = get_member_rank_index(interaction.user, interaction.guild)
        if performer_rank_idx == -1:
            perf_db = get_user(interaction.user.id)
            if perf_db and perf_db.get("rank") in settings.ranks:
                performer_rank_idx = settings.ranks.index(perf_db["rank"])
        if performer_rank_idx == -1:
            await interaction.response.send_message(
                components=[v2_msg("Не удалось определить ваше звание. Повышение недоступно.")],
                ephemeral=True,
            )
            return
        if target_idx >= performer_rank_idx:
            await interaction.response.send_message(
                components=[v2_msg(f"Вы не можете повысить сотрудника, так как его звание ({current_rank}) выше или равно вашему.")],
                ephemeral=True
            )
            return
        valid_ranks = settings.ranks[target_idx + 1: performer_rank_idx + 1]
        if not valid_ranks:
            await interaction.response.send_message(
                components=[v2_msg("Нет доступных званий для повышения.")],
                ephemeral=True
            )
            return

        last_promo = get_last_promotion_time(target.id)
        notice = promotion_cooldown_notice(target.id, current_rank)
        cd_warning = f"\n\n{notice}" if notice else ""

        last_promo_str = "—"
        if last_promo:
            if last_promo.tzinfo is None:
                last_promo = last_promo.replace(tzinfo=datetime.now().astimezone().tzinfo)
            last_promo_str = f"<t:{int(last_promo.timestamp())}:f>"

        joined_str = "—"
        if hasattr(target, "joined_at") and target.joined_at:
            joined_str = f"<t:{int(target.joined_at.timestamp())}:D>"

        info_text = (
            f"### Информация о сотруднике\n\n"
            f"**Сотрудник:** {target.mention} (`{target}`)\n"
            f"**Никнейм:** {nickname}\n"
            f"**Статик:** `{static_id}`\n"
            f"**Текущее звание:** {current_rank}\n"
            f"**Последнее повышение:** {last_promo_str}\n"
            f"**На сервере с:** {joined_str}"
            f"{cd_warning}\n\n"
            f"Вы уверены, что хотите повысить этого сотрудника?"
        )

        needs_static = static_id in ("Не указан", None, "")
        _set_audit_session(interaction.user.id, interaction.guild.id, {
            "target_id": target.id,
            "action": "Promote",
            "old_rank": current_rank,
            "static_id": static_id,
            "valid_ranks": valid_ranks,
            "needs_static": needs_static,
        })

        confirm_view = AuditPromoteConfirmView(valid_ranks=valid_ranks, needs_static=needs_static)
        action_row = disnake.ui.ActionRow(*confirm_view.children)
        card = disnake.ui.Container(
            disnake.ui.TextDisplay(info_text),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await interaction.response.send_message(components=[card], ephemeral=True)
        msg = await interaction.original_message()
        interaction.bot._connection.store_view(confirm_view, msg.id)

class AuditPromoteConfirmView(disnake.ui.View):
    def __init__(self, valid_ranks: list[str], needs_static: bool):
        super().__init__(timeout=300)
        self.valid_ranks = valid_ranks
        self.needs_static = needs_static

    @disnake.ui.button(label="Продолжить", style=disnake.ButtonStyle.success, custom_id="audit:promote_confirm")
    async def confirm(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        session = _get_audit_session(interaction.user.id, interaction.guild.id)
        if not session:
            await interaction.response.send_message(components=[v2_msg("Сессия истекла. Начните выбор заново.")], ephemeral=True)
            return
        valid_ranks = session.get("valid_ranks", self.valid_ranks)
        needs_static = session.get("needs_static", self.needs_static)
        rank_view = AuditSelectRankView("Promote", valid_ranks, needs_static)
        action_row = disnake.ui.ActionRow(*rank_view.children)
        prompt = "### Выберите новое звание"
        notice = promotion_cooldown_notice(session["target_id"], session.get("old_rank", ""))
        if notice:
            prompt = f"{prompt}\n\n{notice}"
        card = disnake.ui.Container(
            disnake.ui.TextDisplay(prompt),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        msg = await interaction.response.edit_message(components=[card])
        interaction.bot._connection.store_view(rank_view, interaction.message.id)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.danger, custom_id="audit:promote_cancel")
    async def cancel(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.edit_message(
            components=[disnake.ui.Container(
                disnake.ui.TextDisplay("Действие отменено."),
                accent_colour=disnake.Colour(0x2C2F33)
            )]
        )

class AuditTransferUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.user_select(placeholder="Выберите сотрудника...", custom_id="audit:select_user_transfer")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        await interaction.response.defer(ephemeral=True)
        target = select.values[0]
        guild = interaction.guild
        if not can_manage_audit(interaction.user):
            await interaction.followup.send(components=[v2_msg("Недостаточно прав. ")], ephemeral=True)
            return
        if interaction.user.id == target.id:
            await interaction.followup.send(components=[v2_msg("Нельзя переводить самого себя.")], ephemeral=True)
            return
        if not isinstance(target, disnake.Member):
            target = guild.get_member(target.id)
            if not target:
                await interaction.followup.send(components=[v2_msg("Пользователь не найден на сервере.")], ephemeral=True)
                return
        current_dept = member_department(target, guild)
        app = get_user(target.id)
        if not app:
            await interaction.followup.send(components=[v2_msg("Пользователь не найден в БД.")], ephemeral=True)
            return
        static_id = app["static_id"]
        _set_audit_session(interaction.user.id, interaction.guild.id, {
            "target_id": target.id,
            "action": "Transfer",
            "static_id": static_id,
            "old_department": current_dept,
            "old_rank": app["rank"]
        })
        options = []
        for dept_name in list_departments():
            options.append(disnake.SelectOption(label=dept_name, value=dept_name))
        if not options:
            await interaction.followup.send(
                components=[v2_msg("Нет доступных отделов для перевода.")],
                ephemeral=True
            )
            return
        select_menu = disnake.ui.Select(
            placeholder="Выберите новый отдел...",
            options=options,
            custom_id="audit_select_department_persistent"
        )
        view = disnake.ui.View(timeout=None)
        view.add_item(select_menu)
        async def _dept_callback(inter: disnake.MessageInteraction):
            session = _get_audit_session(inter.user.id, inter.guild.id)
            if not session:
                await inter.response.send_message(components=[v2_msg("Сессия истекла.")], ephemeral=True)
                return
            selected_dept = inter.values[0]
            if selected_dept == session["old_department"]:
                await inter.response.send_message(
                    components=[v2_msg(f"Сотрудник уже состоит в {selected_dept}! Выберите другой отдел.")],
                    ephemeral=True
                )
                return
            session["new_department"] = selected_dept
            needs_static = session.get("static_id") in ("Не указан", None, "")
            await inter.response.send_modal(AuditTransferReasonModal(needs_static=needs_static))
        select_menu.callback = _dept_callback
        action_row = disnake.ui.ActionRow(select_menu)
        container = disnake.ui.Container(
            disnake.ui.TextDisplay(
                f"Сотрудник: {target.mention}\n"
                f"Static ID: {static_id}\n"
                f"Текущий отдел: {current_dept}\n\n"
                f"Выберите новый отдел для перевода:"
            ),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await interaction.followup.send(components=[container], ephemeral=True)
class AuditTransferReasonModal(disnake.ui.Modal):
    def __init__(self, needs_static: bool = False):
        components = []
        if needs_static:
            components.append(disnake.ui.TextInput(
                label="Static ID",
                custom_id="static_id",
                placeholder="Например: 123456",
                required=True,
                max_length=20
            ))
        components.append(disnake.ui.TextInput(
            label="Причина перевода",
            custom_id="reason",
            required=True,
            max_length=500,
            style=disnake.TextInputStyle.paragraph
        ))
        super().__init__(title="Перевод сотрудника", components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        performer = interaction.user
        guild = interaction.guild
        reason_val = interaction.text_values["reason"].strip()
        session = _get_audit_session(performer.id, guild.id)
        if not session:
            await interaction.followup.send(components=[v2_msg("Сессия истекла.")], ephemeral=True)
            return
        target_id = session["target_id"]
        static_id_input = interaction.text_values.get("static_id")
        if static_id_input:
            session["static_id"] = static_id_input.strip()
            user_db = get_user(target_id)
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], session["static_id"], user_db["rank"], user_db["status"])
        static_id = session["static_id"]
        old_dept = session["old_department"]
        new_dept = session["new_department"]
        old_rank = session["old_rank"]
        target = guild.get_member(target_id)
        if not target:
            await interaction.followup.send(components=[v2_msg("Сотрудник не найден.")], ephemeral=True)
            return
        bot_member = guild.get_member(interaction.client.user.id)
        from utils.helpers import sync_user_roles_and_nickname
        issued_roles, removed_roles_list, errors = await sync_user_roles_and_nickname(target, guild, old_rank, bot_member, override_dept=new_dept)
        add_audit_record(
            action="Перевод",
            target_user_id=target.id,
            target_user_name=str(target),
            target_static_id=static_id,
            target_rank="",
            target_position=new_dept,
            method="",
            reason=f"Из {old_dept} в {new_dept}. {reason_val}",
            performed_by_id=performer.id,
            performed_by_name=str(performer),
            issued_roles=", ".join(issued_roles) if issued_roles else "Нет",
            removed_roles=", ".join(removed_roles_list) if removed_roles_list else "Нет",
        )
        await post_audit_container(
            guild,
            build_audit_container(
                "переводит", performer, target, static_id,
                old_department=old_dept, new_department=new_dept,
                reason=reason_val,
                issued_roles=", ".join(issued_roles) if issued_roles else None,
                removed_roles=", ".join(removed_roles_list) if removed_roles_list else None,
            )
        )
        staff_title = get_staff_title(performer, guild)
        desc_dm = (
            f"### Уведомление о переводе\n\n"
            f"Вы были **переведены** {staff_title}.\n"
            f"> **Из отдела:** {old_dept}\n"
            f"> **В отдел:** {new_dept}\n"
            f"> **Причина:** {reason_val}"
        )
        dm_container = disnake.ui.Container(
            disnake.ui.TextDisplay(desc_dm),
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await send_dm(target, components=[dm_container])
        logger.info(
            "ПЕРЕВОД | Сотрудник: %s (ID: %s) | Static ID: %s | Из: %s | В: %s | Причина: %s | Выполнил: %s (ID: %s)",
            target, target.id, static_id, old_dept, new_dept, reason_val, performer, performer.id
        )
        response = f"{target.mention}: {old_dept} → {new_dept}"
        if errors:
            response += f"\nОшибки: {', '.join(errors)}"
        await interaction.followup.send(components=[v2_msg(response)], ephemeral=True)
class AuditBlacklistAddModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Discord ID пользователя",
                custom_id="target_id",
                required=True,
                max_length=25
            ),
            disnake.ui.TextInput(
                label="Причина занесения",
                custom_id="reason",
                required=True,
                max_length=150
            ),
            disnake.ui.TextInput(
                label="Срок ЧС",
                custom_id="duration",
                required=False,
                placeholder="Например: 15 дней, 2 месяца, навсегда",
                max_length=50
            ),
            disnake.ui.TextInput(
                label="Static ID (Если известен)",
                custom_id="static_id",
                required=False,
                max_length=20
            ),
            disnake.ui.TextInput(
                label="Никнейм (Если известен)",
                custom_id="nickname",
                required=False,
                max_length=50
            )
        ]
        super().__init__(title="Внесение в ЧС", components=components)
    
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(interaction.text_values["target_id"].strip())
        except ValueError:
            await interaction.followup.send(components=[v2_msg("Неверный формат Discord ID.")], ephemeral=True)
            return
            
        reason = interaction.text_values["reason"].strip()
        duration_str = interaction.text_values.get("duration", "").strip()
        static_id = interaction.text_values.get("static_id", "").strip() or "Не указан"
        nickname = interaction.text_values.get("nickname", "").strip()
        
        if not nickname:
            user = interaction.guild.get_member(target_id)
            if user:
                nickname = user.display_name
            else:
                nickname = "Неизвестно"
                
        from utils.helpers import give_blacklist
        member = interaction.guild.get_member(target_id)
        bl_errors = await give_blacklist(
            interaction.guild,
            member,
            user_id=target_id,
            nickname=nickname,
            static_id=static_id,
            reason=reason,
            duration_str=duration_str,
            added_by=interaction.user,
        )
        done = f"Пользователь <@{target_id}> занесен в ЧС."
        if bl_errors:
            done += "\nОшибки: " + ", ".join(bl_errors)
        await interaction.followup.send(components=[v2_msg(done)], ephemeral=True)

class AuditBlacklistRemoveModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Discord ID пользователя",
                custom_id="target_id",
                required=True,
                max_length=25
            ),
            disnake.ui.TextInput(
                label="Причина вынесения",
                custom_id="reason",
                required=True,
                max_length=150
            )
        ]
        super().__init__(title="Вынесение из ЧС", components=components)
        
    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(interaction.text_values["target_id"].strip())
        except ValueError:
            await interaction.followup.send(components=[v2_msg("Неверный формат Discord ID.")], ephemeral=True)
            return
            
        reason = interaction.text_values["reason"].strip()
        
        from database import is_blacklisted
        from utils.helpers import take_blacklist
        
        if not is_blacklisted(target_id):
            await interaction.followup.send(components=[v2_msg(f"Пользователь <@{target_id}> не состоит в ЧС.")], ephemeral=True)
            return
            
        bl_errors = await take_blacklist(
            interaction.guild,
            target_id,
            reason=reason,
            actor=interaction.user,
        )
        done = f"Пользователь <@{target_id}> вынесен из ЧС."
        if bl_errors:
            done += "\nОшибки: " + ", ".join(bl_errors)
        await interaction.followup.send(components=[v2_msg(done)], ephemeral=True)

class AuditActionView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.select(
        placeholder="Выберите действие кадрового аудита...",
        options=[
            disnake.SelectOption(label="Принять", value="Accept", description="Оформление нового сотрудника"),
            disnake.SelectOption(label="Уволить", value="Dismiss", description="Увольнение сотрудника"),
            disnake.SelectOption(label="Понизить в звании", value="Demote", description="Понижение в звании"),
            disnake.SelectOption(label="Повысить в звании", value="Promote", description="Повышение в звании"),
            disnake.SelectOption(label="Перевести", value="Transfer", description="Перевод в другой отдел"),
            disnake.SelectOption(label="Занести в ЧС", value="BlacklistAdd", description="Внесение в чёрный список"),
            disnake.SelectOption(label="Вынести из ЧС", value="BlacklistRemove", description="Вынесение из чёрного списка"),
        ],
        custom_id="audit_action_select"
    )
    async def select_callback(self, select: disnake.ui.Select, interaction: disnake.MessageInteraction):
        selected_value = select.values[0]
        if selected_value == "BlacklistAdd":
            await interaction.response.send_modal(AuditBlacklistAddModal())
            return
        elif selected_value == "BlacklistRemove":
            await interaction.response.send_modal(AuditBlacklistRemoveModal())
            return
            
        await interaction.response.defer(ephemeral=True)
        view = None
        text = ""
        if selected_value == "Accept":
            view = AuditAcceptUserSelectView()
            text = "Выберите пользователя для принятия на службу:"
        elif selected_value == "Dismiss":
            view = AuditDismissUserSelectView()
            text = "Выберите сотрудника для увольнения:"
        elif selected_value == "Demote":
            view = AuditDemoteUserSelectView()
            text = "Выберите сотрудника для понижения в звании:"
        elif selected_value == "Promote":
            view = AuditPromoteUserSelectView()
            text = "Выберите сотрудника для повышения в звании:"
        elif selected_value == "Transfer":
            view = AuditTransferUserSelectView()
            text = "Выберите сотрудника для перевода:"
        if view and text:
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay(text),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            msg = await interaction.followup.send(components=[container], ephemeral=True, wait=True)
            interaction.bot._connection.store_view(view, msg.id)
class AuditCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    async def init_panel(self):
        await send_v2_panel(self.bot, settings.audit_panel_channel_id, "audit")
    @commands.Cog.listener()
    async def on_message_interaction(self, interaction: disnake.MessageInteraction):
        if interaction.data.custom_id != "audit_select_department_persistent":
            return
        session = _get_audit_session(interaction.user.id, interaction.guild.id)
        if not session:
            await interaction.response.send_message(components=[v2_msg("Сессия истекла.")], ephemeral=True)
            return
        selected_dept = interaction.values[0]
        if selected_dept == session["old_department"]:
            await interaction.response.send_message(
                components=[v2_msg(f"Сотрудник уже состоит в {selected_dept}! Выберите другой отдел.")],
                ephemeral=True
            )
            return
        session["new_department"] = selected_dept
        needs_static = session.get("static_id") in ("Не указан", None, "")
        await interaction.response.send_modal(AuditTransferReasonModal(needs_static=needs_static))
def setup(bot):
    bot.add_cog(AuditCog(bot))