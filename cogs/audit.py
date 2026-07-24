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
    is_rank_sergeant_or_above,
)
from utils.panel_init import send_v2_panel

logger = logging.getLogger("bot.audit")

AUDIT_SESSIONS: dict[int, dict] = {}
_AUDIT_SESSION_TTL = 600

def _set_audit_session(user_id: int, data: dict) -> None:
    _cleanup_audit_sessions()
    data["_ts"] = time.monotonic()
    AUDIT_SESSIONS[user_id] = data


def _get_audit_session(user_id: int) -> dict | None:
    session = AUDIT_SESSIONS.get(user_id)
    if not session:
        return None
    if time.monotonic() - session.get("_ts", 0) > _AUDIT_SESSION_TTL:
        AUDIT_SESSIONS.pop(user_id, None)
        return None
    return session


def _cleanup_audit_sessions() -> None:
    now = time.monotonic()
    expired = [
        uid for uid, data in AUDIT_SESSIONS.items()
        if now - data.get("_ts", 0) > _AUDIT_SESSION_TTL
    ]
    for uid in expired:
        del AUDIT_SESSIONS[uid]


async def post_audit_container(guild, container):
    channel = guild.get_channel(settings.audit_log_channel_id)
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

        _set_audit_session(interaction.user.id, {
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

        session = _get_audit_session(performer.id)
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

        _set_audit_session(interaction.user.id, {
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
        super().__init__(title="Увольнение сотрудника", components=components)

    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        performer = interaction.user
        guild = interaction.guild
        reason_val = interaction.text_values["reason"].strip()

        session = _get_audit_session(performer.id)
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
                
        base_name = target.display_name
        if " | " in base_name:
            base_name = base_name.split(" | ", 1)[1]
        try:
            await target.edit(nick=f"Уволен | {base_name}")
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


class AuditPromoteDemoteModal(disnake.ui.Modal):
    def __init__(self, action: str, needs_static: bool = False):
        self.action = action
        title = "Повышение" if action == "Promote" else "Понижение"
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
            label="Новое звание",
            custom_id="new_rank",
            placeholder="Например: Сержант",
            required=True,
            max_length=50
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
        new_rank = interaction.text_values["new_rank"].strip()
        reason_val = interaction.text_values["reason"].strip()

        session = _get_audit_session(performer.id)
        if not session:
            await interaction.followup.send(
                components=[v2_msg("Сессия истекла или не найдена. Начните выбор заново.")],
                ephemeral=True
            )
            return

        target_id = session["target_id"]
        
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

        _set_audit_session(interaction.user.id, {
            "target_id": target.id,
            "action": "Demote",
            "old_rank": user_db["rank"],
            "static_id": user_db["static_id"]
        })
        needs_static = user_db.get("static_id") in ("Не указан", None, "")
        await interaction.response.send_modal(AuditPromoteDemoteModal("Demote", needs_static=needs_static))


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
        if not user_db:
            await interaction.response.send_message(components=[v2_msg("Пользователь не найден в базе данных.")], ephemeral=True)
            return

        _set_audit_session(interaction.user.id, {
            "target_id": target.id,
            "action": "Promote",
            "old_rank": user_db["rank"],
            "static_id": user_db["static_id"]
        })
        needs_static = user_db.get("static_id") in ("Не указан", None, "")
        await interaction.response.send_modal(AuditPromoteDemoteModal("Promote", needs_static=needs_static))


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

        dept_ids = settings.department_role_ids
        current_dept = "Нет"
        for dept_name, role_id in dept_ids.items():
            role = guild.get_role(role_id)
            if role and role in target.roles:
                current_dept = dept_name
                break

        app = get_user(target.id)
        if not app:
            await interaction.followup.send(components=[v2_msg("Пользователь не найден в БД.")], ephemeral=True)
            return
            
        static_id = app["static_id"]

        _set_audit_session(interaction.user.id, {
            "target_id": target.id,
            "action": "Transfer",
            "static_id": static_id,
            "old_department": current_dept,
            "old_rank": app["rank"]
        })

        options = []
        for dept_name in dept_ids:
            if dept_name == current_dept:
                continue
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
        
        # We need a callback for this select menu right here
        async def _dept_callback(inter: disnake.MessageInteraction):
            session = _get_audit_session(inter.user.id)
            if not session:
                await inter.response.send_message(components=[v2_msg("Сессия истекла.")], ephemeral=True)
                return
            needs_static = session.get("static_id") in ("Не указан", None, "")
            await inter.response.send_modal(AuditTransferReasonModal(needs_static=needs_static))
            
        select_menu.callback = _dept_callback
        
        action_row = disnake.ui.ActionRow(*view.children)

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

        session = _get_audit_session(performer.id)
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
        ],
        custom_id="audit_action_select"
    )
    async def select_callback(self, select: disnake.ui.Select, interaction: disnake.MessageInteraction):
        selected_value = select.values[0]
        if selected_value == "Accept":
            view = AuditAcceptUserSelectView()
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay("Выберите пользователя для принятия на службу:"),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await interaction.response.send_message(components=[container], ephemeral=True)
        elif selected_value == "Dismiss":
            view = AuditDismissUserSelectView()
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay("Выберите сотрудника для увольнения:"),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await interaction.response.send_message(components=[container], ephemeral=True)
        elif selected_value == "Demote":
            view = AuditDemoteUserSelectView()
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay("Выберите сотрудника для понижения в звании:"),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await interaction.response.send_message(components=[container], ephemeral=True)
        elif selected_value == "Promote":
            view = AuditPromoteUserSelectView()
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay("Выберите сотрудника для повышения в звании:"),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await interaction.response.send_message(components=[container], ephemeral=True)
        elif selected_value == "Transfer":
            view = AuditTransferUserSelectView()
            user_select_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                disnake.ui.TextDisplay("Выберите сотрудника для перевода:"),
                user_select_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await interaction.response.send_message(components=[container], ephemeral=True)
class AuditCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def init_panel(self):
        await send_v2_panel(self.bot, settings.audit_panel_channel_id, "audit")


def setup(bot):
    bot.add_cog(AuditCog(bot))
