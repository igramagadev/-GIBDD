import logging
from datetime import datetime

import disnake
from disnake.ext import commands

from config.settings import settings
from database import (
    add_application,
    update_application_status,
    get_application_by_message_id,
    add_audit_record,
    get_user,
    add_or_update_user,
    set_user_status,
    is_blacklisted,
)
from utils.helpers import (
    clean_role_name,
    can_manage_role,
    can_manage_applications,
    can_manage_resignations,
    send_dm,
    find_rank_role,
    v2_msg,
    get_cached_val,
    set_cached_val,
    get_staff_title,
)
from utils.interaction_guard import interaction_guard

logger = logging.getLogger("bot.applications")


def build_application_container(
    app_id: int | str,
    user: disnake.Member | disnake.User,
    nickname: str,
    static_id: str,
    method: str,
    rank: str,
    status_text: str,
    docs: str = "",
    action_row: disnake.ui.ActionRow = None
) -> disnake.ui.Container:
    joined_at = getattr(user, "joined_at", None)
    if joined_at:
        joined_timestamp = int(joined_at.timestamp())
        joined_str = f"<t:{joined_timestamp}:f> (<t:{joined_timestamp}:R>)"
    else:
        joined_str = "Неизвестно"

    profile_text = (
        f"**Пользователь:** {user.mention}\n"
        f"**ID:** `{user.id}`\n"
        f"**Присоединился:** {joined_str}"
    )

    title = f"Заявка на роль #{app_id}" if app_id else "Новая заявка на роль"

    components = [
        disnake.ui.TextDisplay(f"### {title}"),
        disnake.ui.Section(
            disnake.ui.TextDisplay(profile_text),
            accessory=disnake.ui.Thumbnail(media=user.display_avatar.url)
        ),
        disnake.ui.Separator()
    ]

    fields_text = (
        f"**Никнейм (игровой ник):**\n```\n{nickname}\n```\n"
        f"**Static ID:**\n```\n{static_id}\n```\n"
        f"**Способ подачи:**\n```\n{method}\n```\n"
        f"**Желаемое звание:**\n```\n{rank}\n```"
    )

    if docs:
        fields_text += f"\n\n**Ссылка на документы:**\n{docs}"

    components.append(disnake.ui.TextDisplay(fields_text))

    if docs and (docs.startswith("http://") or docs.startswith("https://")):
        components.append(disnake.ui.Separator())
        components.append(disnake.ui.MediaGallery(disnake.ui.MediaGalleryItem(media=docs)))

    components.append(disnake.ui.Separator())
    components.append(disnake.ui.TextDisplay(f"**Статус:** {status_text}"))

    if action_row:
        components.append(action_row)

    return disnake.ui.Container(*components, accent_colour=disnake.Colour(0x2C2F33))


def build_resignation_container(
    app_id: int | str,
    user: disnake.Member | disnake.User,
    nickname: str,
    static_id: str,
    rank: str,
    reason: str,
    status_text: str,
    guild: disnake.Guild = None,
    action_row: disnake.ui.ActionRow = None
) -> disnake.ui.Container:
    joined_at = getattr(user, "joined_at", None)
    if joined_at:
        joined_timestamp = int(joined_at.timestamp())
        joined_str = f"<t:{joined_timestamp}:f> (<t:{joined_timestamp}:R>)"
    else:
        joined_str = "Неизвестно"

    profile_text = (
        f"**Пользователь:** {user.mention}\n"
        f"**ID:** `{user.id}`\n"
        f"**Присоединился:** {joined_str}"
    )
    
    title = f"Заявление на увольнение #{app_id}" if app_id else "Заявление на увольнение"

    leader_mention = "@Начальник"
    if guild:
        leader_role = disnake.utils.get(guild.roles, name="Начальник УГИБДД")
        leader = leader_role.members[0] if leader_role and leader_role.members else guild.owner
        if leader:
            leader_mention = leader.mention

    date_str = datetime.now().strftime("%d.%m.%Y")
    statement_text = (
        "Начальнику Управления ГИБДД ГУ МВД по г. Москве и Московской области Генерал-лейтенанту полиции\n"
        f"{leader_mention}\n\n"
        f"от {rank} полиции {nickname}\n\n"
        "**Заявление**\n\n"
        f"Я, {nickname}, находящийся в звании {rank} полиции, служебное удостоверение № {static_id} "
        f"прошу Вас рассмотреть моё заявление на увольнение из рядов Управления Государственной Инспекции Безопасности "
        f"Дорожного Движения по причине: {reason}.\n\n"
        f"Дата: {date_str}"
    )

    components = [
        disnake.ui.TextDisplay(f"### {title}"),
        disnake.ui.Section(
            disnake.ui.TextDisplay(profile_text),
            accessory=disnake.ui.Thumbnail(media=user.display_avatar.url)
        ),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(statement_text)
    ]

    components.append(disnake.ui.Separator())
    components.append(disnake.ui.TextDisplay(f"**Статус:** {status_text}"))

    if action_row:
        components.append(action_row)

    return disnake.ui.Container(*components, accent_colour=disnake.Colour(0x2C2F33))


class RejectionReasonModal(disnake.ui.Modal):
    def __init__(self, app_data, interaction_message):
        self.app_data = app_data
        self.interaction_message = interaction_message

        components = [
            disnake.ui.TextInput(
                label="Причина отклонения",
                custom_id="reason",
                placeholder="Укажите причину отклонения заявки",
                required=True,
                max_length=500,
                style=disnake.TextInputStyle.paragraph,
            )
        ]
        super().__init__(title="Причина отклонения", components=components)

    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        app_id, user_id, user_name, nickname, static_id, rank, method, status, *_ = self.app_data
        reason_val = interaction.text_values["reason"].strip()

        update_application_status(app_id, "rejected", member.id, str(member))

        target = guild.get_member(user_id)
        if not target:
            try:
                target = await interaction.client.fetch_user(user_id)
            except Exception:
                target = interaction.user

        docs_url = self.app_data[13] if len(self.app_data) > 13 else ""
        staff_title = get_staff_title(member, guild)

        container = build_application_container(
            app_id=app_id,
            user=target,
            nickname=nickname,
            static_id=static_id,
            method=method,
            rank=rank,
            status_text=f"Отклонено {staff_title}\nПричина: {reason_val}",
            docs=docs_url
        )
        await self.interaction_message.edit(components=[container])

        logger.info(
            "ЗАЯВКА ОТКЛОНЕНА | Номер: #%s | Пользователь: %s (%s) | Ник: %s | Static ID: %s | Звание: %s | Способ: %s | Отклонил: %s (ID: %s) | Причина: %s",
            app_id, user_name, user_id, nickname, static_id, rank, method, member, member.id, reason_val
        )

        dm_status = "ЛС закрыты"
        if target:
            desc_dm = (
                f"### Уведомление по заявке #{app_id}\n\n"
                f"Ваша заявка на роль была **отклонена** {staff_title}.\n"
                f"> **Причина:** {reason_val}\n"
                f"> **Сервер:** {guild.name}"
            )
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay(desc_dm),
                accent_colour=disnake.Colour(0x2C2F33)
            )
            dm_sent = await send_dm(target, components=[dm_container])
            if dm_sent:
                dm_status = "ЛС отправлены"
        else:
            dm_status = "Пользователь покинул сервер"

        await interaction.followup.send(
            components=[v2_msg(f"Заявка отклонена. Причина: {reason_val}\n{dm_status}")],
            ephemeral=True,
        )

class ApplicationRoleSelectView(disnake.ui.View):
    def __init__(self, app_id: int, target_id: int, performer_id: int, interaction_message: disnake.Message, app_data: tuple):
        super().__init__(timeout=300)
        self.app_id = app_id
        self.target_id = target_id
        self.performer_id = performer_id
        self.interaction_message = interaction_message
        self.app_data = app_data

    @disnake.ui.role_select(placeholder="Выберите роли для выдачи...", min_values=1, max_values=10, custom_id="app_manual_role_select")
    async def select_roles(self, select: disnake.ui.RoleSelect, interaction: disnake.MessageInteraction):
        if interaction.user.id != self.performer_id:
            await interaction.response.send_message(components=[v2_msg("Только модератор, одобряющий заявку, может выбрать роли.")], ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        target = guild.get_member(self.target_id)
        
        if not target:
            await interaction.followup.send(components=[v2_msg("Пользователь покинул сервер.")], ephemeral=True)
            return

        app_id, user_id, user_name, nickname, static_id, rank, method, status, *_ = self.app_data
        
        bot_member = guild.get_member(interaction.client.user.id)
        errors = []
        issued_roles = []
        
        for role in select.values:
            if role not in target.roles:
                if can_manage_role(bot_member, role):
                    try:
                        await target.add_roles(role)
                        issued_roles.append(clean_role_name(role.name))
                    except Exception as exc:
                        errors.append(f"{role.name}: {exc}")
                else:
                    errors.append(f"Роль '{role.name}' выше бота")

        update_application_status(self.app_id, "issued", member.id, str(member))
        add_or_update_user(target.id, nickname, static_id, rank, "active")

        staff_title = get_staff_title(member, guild)

        status_text = f"Одобрено {staff_title}"
        if issued_roles:
            status_text += f"\nВыданные роли: {', '.join(issued_roles)}"
        if errors:
            status_text += f"\nОшибки: {', '.join(errors)}"

        docs_url = self.app_data[13] if len(self.app_data) > 13 else ""
        new_container = build_application_container(
            app_id=self.app_id,
            user=target,
            nickname=nickname,
            static_id=static_id,
            method=method,
            rank=rank,
            status_text=status_text,
            docs=docs_url
        )
        await self.interaction_message.edit(components=[new_container])

        logger.info(
            "ЗАЯВКА ОДОБРЕНА + РОЛИ ВРУЧНУЮ | Номер: #%s | Пользователь: %s (%s) | Ник: %s | Звание: %s | Одобрил: %s (ID: %s) | Выданные роли: %s",
            self.app_id, user_name, user_id, nickname, rank, member, member.id, ", ".join(issued_roles) if issued_roles else "Нет"
        )

        desc_dm = (
            f"### Уведомление по заявке #{self.app_id}\n\n"
            f"Ваша заявка на роль была **одобрена** {staff_title}.\n"
            f"> **Звание:** {rank}\n"
            f"> **Выданные роли:** {', '.join(issued_roles) if issued_roles else 'Нет'}"
        )
        dm_container = disnake.ui.Container(
            disnake.ui.TextDisplay(desc_dm),
            accent_colour=disnake.Colour(0x2C2F33)
        )
        dm_sent = await send_dm(target, components=[dm_container])
        dm_status = "ЛС отправлены" if dm_sent else "ЛС закрыты"

        response_msg = f"Заявка одобрена! Выбранные роли выданы {target.mention}.\n{dm_status}"
        if errors:
            response_msg += f"\nОшибки: {', '.join(errors)}"

        await interaction.followup.send(components=[v2_msg(response_msg)], ephemeral=True)
        self.stop()


class ApplicationActionView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Одобрить", style=disnake.ButtonStyle.success, custom_id="approve_app")
    async def approve_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.defer(ephemeral=True)
        async with interaction_guard.lock(interaction.message.id) as acquired:
            if not acquired:
                await interaction.followup.send(
                    components=[v2_msg("Эту заявку уже обрабатывает другой модератор.")],
                    ephemeral=True,
                )
                return
            guild = interaction.guild
            member = interaction.user

            if not can_manage_applications(member):
                await interaction.followup.send(
                    components=[v2_msg("Недостаточно прав. ")],
                    ephemeral=True
                )
                return

            app = get_application_by_message_id(interaction.message.id)
            if not app:
                await interaction.followup.send(components=[v2_msg("Заявка не найдена в базе данных.")], ephemeral=True)
                return

            app_id, user_id, user_name, nickname, static_id, rank, method, status, *_ = app
            if status != "pending":
                await interaction.followup.send(components=[v2_msg(f"Заявка уже обработана (статус: {status}).")], ephemeral=True)
                return

            if member.id == user_id:
                await interaction.followup.send(components=[v2_msg("Нельзя одобрять свою собственную заявку.")], ephemeral=True)
                return

            user_db = get_user(user_id)


            if is_blacklisted(user_id):
                await interaction.followup.send(
                    components=[v2_msg("Пользователь в Чёрном Списке! Обработка заблокирована.")],
                    ephemeral=True
                )
                return

            if user_db and user_db["status"] == "active":
                await interaction.followup.send(
                    components=[v2_msg("Данный сотрудник уже трудоустроен!")],
                    ephemeral=True
                )
                return

            target = guild.get_member(user_id)
            if not target:
                await interaction.followup.send(
                    components=[v2_msg(f"Пользователь с ID {user_id} покинул сервер.")],
                    ephemeral=True,
                )
                return

            bot_member = guild.get_member(interaction.client.user.id)
            errors = []
            issued_roles = []
            removed_roles_list = []

            rank_role = find_rank_role(guild, rank)
            if not rank_role:
                view = ApplicationRoleSelectView(
                    app_id=app_id,
                    target_id=target.id,
                    performer_id=member.id,
                    interaction_message=interaction.message,
                    app_data=app
                )
                action_row = disnake.ui.ActionRow(*view.children)
                container = disnake.ui.Container(
                    disnake.ui.TextDisplay(f"Роль звания '{rank}' не найдена.\nПожалуйста, выберите роли для выдачи вручную:"),
                    action_row,
                    accent_colour=disnake.Colour(0x2C2F33)
                )
                msg = await interaction.followup.send(components=[container], ephemeral=True, wait=True)
                interaction.bot._connection.store_view(view, msg.id)
                return

            base_role = guild.get_role(settings.base_role_id)
            if base_role and base_role not in target.roles:
                if can_manage_role(bot_member, base_role):
                    try:
                        await target.add_roles(base_role)
                        issued_roles.append(clean_role_name(base_role.name))
                    except Exception as exc:
                        errors.append(f"Базовая роль: {exc}")
                else:
                    errors.append(f"Базовая роль '{base_role.name}' выше бота")

            cadet_role = guild.get_role(settings.cadet_role_id)
            if rank.lower().strip() in ("рядовой", "мл. сержант", "младший сержант"):
                if cadet_role and cadet_role not in target.roles:
                    if can_manage_role(bot_member, cadet_role):
                        try:
                            await target.add_roles(cadet_role)
                            issued_roles.append(clean_role_name(cadet_role.name))
                        except Exception as exc:
                            errors.append(f"Курсант: {exc}")
                    else:
                        errors.append("Роль курсанта выше бота")
            else:
                if cadet_role and cadet_role in target.roles:
                    if can_manage_role(bot_member, cadet_role):
                        try:
                            await target.remove_roles(cadet_role)
                            removed_roles_list.append(clean_role_name(cadet_role.name))
                        except Exception as exc:
                            errors.append(f"Снятие курсанта: {exc}")

            if rank_role and rank_role not in target.roles:
                if can_manage_role(bot_member, rank_role):
                    try:
                        await target.add_roles(rank_role)
                        issued_roles.append(clean_role_name(rank_role.name))
                    except Exception as exc:
                        errors.append(f"Звание '{rank}': {exc}")
                else:
                    errors.append(f"Звание '{rank}' выше бота")

            update_application_status(app_id, "issued", member.id, str(member))
            add_or_update_user(target.id, nickname, static_id, rank, "active")

            staff_title = get_staff_title(member, guild)

            status_text = f"Одобрено {staff_title}"
            if issued_roles:
                status_text += f"\nВыданные роли: {', '.join(issued_roles)}"
            if removed_roles_list:
                status_text += f"\nСнятые роли: {', '.join(removed_roles_list)}"
            if errors:
                status_text += f"\nОшибки: {', '.join(errors)}"

            docs_url = app[13] if len(app) > 13 else ""
            new_container = build_application_container(
                app_id=app_id,
                user=target,
                nickname=nickname,
                static_id=static_id,
                method=method,
                rank=rank,
                status_text=status_text,
                docs=docs_url
            )
            await interaction.message.edit(components=[new_container])

            logger.info(
                "ЗАЯВКА ОДОБРЕНА + РОЛИ ВЫДАНЫ | Номер: #%s | Пользователь: %s (%s) | Ник: %s | Static ID: %s | Звание: %s | Способ: %s | Одобрил: %s (ID: %s) | Выданные роли: %s",
                app_id, user_name, user_id, nickname, static_id, rank, method, member, member.id, ", ".join(issued_roles) if issued_roles else "Нет"
            )

            desc_dm = (
                f"### Уведомление по заявке #{app_id}\n\n"
                f"Ваша заявка на роль была **одобрена** {staff_title}.\n"
                f"> **Звание:** {rank}\n"
                f"> **Выданные роли:** {', '.join(issued_roles) if issued_roles else 'Нет'}"
            )
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay(desc_dm),
                accent_colour=disnake.Colour(0x2C2F33)
            )
            dm_sent = await send_dm(target, components=[dm_container])
            dm_status = "ЛС отправлены" if dm_sent else "ЛС закрыты"

            response_msg = f"Заявка одобрена! Роли выданы {target.mention}.\n{dm_status}"
            if errors:
                response_msg += f"\nОшибки: {', '.join(errors)}"

            await interaction.followup.send(components=[v2_msg(response_msg)], ephemeral=True)

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="reject_app")
    async def reject_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        async with interaction_guard.lock(interaction.message.id) as acquired:
            if not acquired:
                await interaction.response.send_message(
                    components=[v2_msg("Эту заявку уже обрабатывает другой модератор.")],
                    ephemeral=True,
                )
                return
            member = interaction.user

            if not can_manage_applications(member):
                await interaction.response.send_message(
                    components=[v2_msg("Недостаточно прав. ")],
                    ephemeral=True
                )
                return

            app = get_application_by_message_id(interaction.message.id)
            if not app:
                await interaction.response.send_message(components=[v2_msg("Заявка не найдена.")], ephemeral=True)
                return

            _, user_id, _, _, _, _, _, status, *_ = app
            if status != "pending":
                await interaction.response.send_message(components=[v2_msg(f"Заявка уже обработана (статус: {status}).")], ephemeral=True)
                return

            if member.id == user_id:
                await interaction.response.send_message(components=[v2_msg("Нельзя отклонять свою собственную заявку.")], ephemeral=True)
                return

            modal = RejectionReasonModal(app_data=app, interaction_message=interaction.message)
            await interaction.response.send_modal(modal)


class ApplicationModal(disnake.ui.Modal):
    def __init__(self, user_id: int):
        self.user_id = user_id
        cached_nickname = get_cached_val(user_id, "ApplicationModal", "nickname", "")
        cached_static_id = get_cached_val(user_id, "ApplicationModal", "static_id", "")
        cached_method = get_cached_val(user_id, "ApplicationModal", "method", "")
        cached_rank = get_cached_val(user_id, "ApplicationModal", "desired_rank", "")
        cached_docs = get_cached_val(user_id, "ApplicationModal", "docs", "")

        components = [
            disnake.ui.TextInput(
                label="Никнейм (игровой ник)",
                custom_id="nickname",
                required=True,
                max_length=50,
                value=cached_nickname
            ),
            disnake.ui.TextInput(
                label="Static ID",
                custom_id="static_id",
                required=True,
                max_length=50,
                value=cached_static_id
            ),
            disnake.ui.TextInput(
                label="Способ подачи",
                custom_id="method",
                placeholder="Собеседование / Электронная заявка / После КМБ",
                required=True,
                max_length=30,
                value=cached_method
            ),
            disnake.ui.TextInput(
                label="Желаемое звание",
                custom_id="desired_rank",
                required=True,
                max_length=50,
                value=cached_rank
            ),
            disnake.ui.TextInput(
                label="Ссылка на документы",
                custom_id="docs",
                placeholder="Например: https://imgur.com/...",
                required=False,
                max_length=200,
                value=cached_docs
            )
        ]
        super().__init__(title="Заявка на роль", components=components)

    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        method_str = interaction.text_values["method"].strip()
        rank_str = interaction.text_values["desired_rank"].strip()
        nickname_str = interaction.text_values["nickname"].strip()
        static_id_str = interaction.text_values["static_id"].strip()
        docs_str = interaction.text_values.get("docs", "").strip()

        set_cached_val(self.user_id, "ApplicationModal", "nickname", nickname_str)
        set_cached_val(self.user_id, "ApplicationModal", "static_id", static_id_str)
        set_cached_val(self.user_id, "ApplicationModal", "desired_rank", rank_str)
        set_cached_val(self.user_id, "ApplicationModal", "method", method_str)
        set_cached_val(self.user_id, "ApplicationModal", "docs", docs_str)

        review_channel = guild.get_channel(settings.application_review_channel_id)
        if not review_channel:
            await interaction.followup.send(
                components=[v2_msg("Канал для заявок не найден! Обратитесь к администратору.")],
                ephemeral=True,
            )
            return

        view = ApplicationActionView()
        action_row = disnake.ui.ActionRow(*view.children)
        container = build_application_container(
            app_id="",
            user=user,
            nickname=nickname_str,
            static_id=static_id_str,
            method=method_str,
            rank=rank_str,
            status_text="Ожидает рассмотрения",
            docs=docs_str,
            action_row=action_row
        )
        app_message = await review_channel.send(components=[container])

        app_id = add_application(
            user_id=user.id,
            user_name=str(user),
            nickname=nickname_str,
            static_id=static_id_str,
            rank=rank_str,
            method=method_str,
            message_id=app_message.id,
            docs=docs_str
        )

        container_with_id = build_application_container(
            app_id=app_id,
            user=user,
            nickname=nickname_str,
            static_id=static_id_str,
            method=method_str,
            rank=rank_str,
            status_text="Ожидает рассмотрения",
            docs=docs_str,
            action_row=action_row
        )
        await app_message.edit(components=[container_with_id])

        logger.info(
            "НОВАЯ ЗАЯВКА | Номер: #%s | Пользователь: %s (ID: %s) | Ник: %s | Static ID: %s | Желаемое звание: %s | Способ: %s",
            app_id, user, user.id, nickname_str, static_id_str, rank_str, method_str
        )

        await interaction.followup.send(
            components=[v2_msg(f"Заявка отправлена. Номер: #{app_id}")],
            ephemeral=True,
        )


class ResignationModal(disnake.ui.Modal):
    def __init__(self, user_data=None, user_id: int = 0):
        self.user_data = user_data
        self.user_id = user_id

        cached_reason = get_cached_val(user_id, "ResignationModal", "reason", "")

        if user_data:
            self.nickname, self.static_id, self.rank = user_data
            components = [
                disnake.ui.TextInput(
                    label="Причина увольнения",
                    custom_id="reason",
                    required=True,
                    max_length=500,
                    style=disnake.TextInputStyle.paragraph,
                    value=cached_reason
                )
            ]
        else:
            cached_nickname = get_cached_val(user_id, "ResignationModal", "nickname", "")
            cached_static_id = get_cached_val(user_id, "ResignationModal", "static_id", "")
            cached_rank = get_cached_val(user_id, "ResignationModal", "current_rank", "")

            components = [
                disnake.ui.TextInput(
                    label="Имя Фамилия (Никнейм)",
                    custom_id="nickname",
                    required=True,
                    max_length=50,
                    value=cached_nickname
                ),
                disnake.ui.TextInput(
                    label="Номер удостоверения (Static ID)",
                    custom_id="static_id",
                    required=True,
                    max_length=50,
                    value=cached_static_id
                ),
                disnake.ui.TextInput(
                    label="Текущее звание",
                    custom_id="current_rank",
                    required=True,
                    max_length=50,
                    value=cached_rank
                ),
                disnake.ui.TextInput(
                    label="Причина увольнения",
                    custom_id="reason",
                    required=True,
                    max_length=500,
                    style=disnake.TextInputStyle.paragraph,
                    value=cached_reason
                )
            ]
        super().__init__(title="Заявление на увольнение", components=components)

    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        if self.user_data:
            nickname_str = self.nickname
            static_id_str = self.static_id
            rank_str = self.rank
            reason_str = interaction.text_values["reason"].strip()
        else:
            nickname_str = interaction.text_values["nickname"].strip()
            static_id_str = interaction.text_values["static_id"].strip()
            rank_str = interaction.text_values["current_rank"].strip()
            reason_str = interaction.text_values["reason"].strip()

        set_cached_val(self.user_id, "ResignationModal", "reason", reason_str)
        if not self.user_data:
            set_cached_val(self.user_id, "ResignationModal", "nickname", nickname_str)
            set_cached_val(self.user_id, "ResignationModal", "static_id", static_id_str)
            set_cached_val(self.user_id, "ResignationModal", "current_rank", rank_str)

        review_channel = guild.get_channel(settings.resignation_review_channel_id)
        if not review_channel:
            await interaction.followup.send(
                components=[v2_msg("Канал для заявлений на увольнение не найден! Обратитесь к администратору.")],
                ephemeral=True,
            )
            return

        view = ResignationActionView()
        action_row = disnake.ui.ActionRow(*view.children)
        container = build_resignation_container(
            app_id="",
            user=user,
            nickname=nickname_str,
            static_id=static_id_str,
            rank=rank_str,
            reason=reason_str,
            status_text="Ожидает рассмотрения",
            guild=guild,
            action_row=action_row
        )
        app_message = await review_channel.send(components=[container])

        app_id = add_application(
            user_id=user.id,
            user_name=str(user),
            nickname=nickname_str,
            static_id=static_id_str,
            rank=rank_str,
            method="Увольнение",
            message_id=app_message.id,
            docs=""
        )

        container_with_id = build_resignation_container(
            app_id=app_id,
            user=user,
            nickname=nickname_str,
            static_id=static_id_str,
            rank=rank_str,
            reason=reason_str,
            status_text="Ожидает рассмотрения",
            guild=guild,
            action_row=action_row
        )
        await app_message.edit(components=[container_with_id])

        logger.info(
            "НОВОЕ ЗАЯВЛЕНИЕ НА УВОЛЬНЕНИЕ | Номер: #%s | Пользователь: %s (ID: %s) | Ник: %s | Static ID: %s | Текущее звание: %s",
            app_id, user, user.id, nickname_str, static_id_str, rank_str
        )

        await interaction.followup.send(
            components=[v2_msg(f"Заявление на увольнение отправлено! Номер: #{app_id}")],
            ephemeral=True,
        )


class ResignationActionView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Одобрить", style=disnake.ButtonStyle.success, custom_id="approve_resignation")
    async def approve_resignation(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await interaction.response.defer(ephemeral=True)
        async with interaction_guard.lock(interaction.message.id) as acquired:
            if not acquired:
                await interaction.followup.send(
                    components=[v2_msg("Это заявление уже обрабатывает другой модератор.")],
                    ephemeral=True,
                )
                return
            guild = interaction.guild
            member = interaction.user

            if not can_manage_resignations(member):
                await interaction.followup.send(
                    components=[v2_msg("Недостаточно прав. ")],
                    ephemeral=True
                )
                return

            app = get_application_by_message_id(interaction.message.id)
            if not app:
                await interaction.followup.send(components=[v2_msg("Заявление не найдено.")], ephemeral=True)
                return

            app_id, user_id, user_name, nickname, static_id, rank, method, status, *_ = app
            if status != "pending":
                await interaction.followup.send(components=[v2_msg(f"Заявление уже обработано (статус: {status}).")], ephemeral=True)
                return

            if member.id == user_id:
                await interaction.followup.send(components=[v2_msg("Нельзя одобрять своё собственное заявление.")], ephemeral=True)
                return

            target = guild.get_member(user_id)
            if not target:
                await interaction.followup.send(
                    components=[v2_msg(f"Пользователь с ID {user_id} покинул сервер.")],
                    ephemeral=True,
                )
                return

            bot_member = guild.get_member(interaction.client.user.id)
            errors = []
            removed_roles_list = []

            cleanup_ids = settings.roles_to_cleanup_ids
            cleanup_names = settings.roles_to_cleanup_names
            for role in target.roles:
                is_cleanup = False
                if cleanup_ids:
                    is_cleanup = role.id in cleanup_ids
                else:
                    is_cleanup = role.name in cleanup_names

                if is_cleanup and can_manage_role(bot_member, role):
                    try:
                        await target.remove_roles(role)
                        removed_roles_list.append(clean_role_name(role.name))
                    except Exception as exc:
                        errors.append(f"{role.name}: {exc}")

            for role_id in (settings.base_role_id, settings.cadet_role_id):
                role = guild.get_role(role_id)
                if role and role in target.roles and can_manage_role(bot_member, role):
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

            update_application_status(app_id, "issued", member.id, str(member))
            set_user_status(target.id, "fired")

            add_audit_record(
                action="Уволить",
                target_user_id=target.id,
                target_user_name=str(target),
                target_static_id=static_id,
                target_rank="",
                target_position="",
                method="Собственное желание",
                reason="Заявление на увольнение",
                performed_by_id=member.id,
                performed_by_name=str(member),
                issued_roles=", ".join(issued_roles_list) if issued_roles_list else "Нет",
                removed_roles=", ".join(removed_roles_list) if removed_roles_list else "Нет",
            )

            from cogs.audit import build_audit_container, post_audit_container
            audit_cont = build_audit_container(
                action_verb="увольняет",
                performer=member,
                target=target,
                static_id=static_id,
                reason="Собственное желание (Заявление)"
            )
            await post_audit_container(guild, audit_cont)

            staff_title = get_staff_title(member, guild)

            status_text = f"Одобрено {staff_title}\nСотрудник уволен"
            if removed_roles_list:
                status_text += f"\nСняты роли: {', '.join(removed_roles_list)}"
            if errors:
                status_text += f"\nОшибки: {', '.join(errors)}"

            new_container = build_resignation_container(
                app_id=app_id,
                user=target,
                nickname=nickname,
                static_id=static_id,
                rank=rank,
                reason="Собственное желание",
                status_text=status_text,
                guild=guild,
            )
            await interaction.message.edit(components=[new_container])

            logger.info(
                "ЗАЯВЛЕНИЕ НА УВОЛЬНЕНИЕ ОДОБРЕНО | Номер: #%s | Сотрудник: %s (ID: %s) | Выполнил: %s (ID: %s) | Снятые роли: %s",
                app_id, target, target.id, member, member.id, ", ".join(removed_roles_list)
            )

            desc_dm = (
                f"### Уведомление об увольнении #{app_id}\n\n"
                f"Ваше заявление на увольнение было **одобрено** {staff_title}.\n"
                f"> **Снятые роли:** {', '.join(removed_roles_list) if removed_roles_list else 'Нет'}"
            )
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay(desc_dm),
                accent_colour=disnake.Colour(0x2C2F33)
            )
            dm_sent = await send_dm(target, components=[dm_container])
            dm_status = "ЛС отправлены" if dm_sent else "ЛС закрыты"

            await interaction.followup.send(
                components=[v2_msg(f"Заявление одобрено, роли с {target.mention} сняты.\n{dm_status}")],
                ephemeral=True
            )

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="reject_resignation")
    async def reject_resignation(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        async with interaction_guard.lock(interaction.message.id) as acquired:
            if not acquired:
                await interaction.response.send_message(
                    components=[v2_msg("Это заявление уже обрабатывает другой модератор.")],
                    ephemeral=True,
                )
                return
            member = interaction.user

            if not can_manage_resignations(member):
                await interaction.response.send_message(
                    components=[v2_msg("Недостаточно прав. ")],
                    ephemeral=True
                )
                return

            app = get_application_by_message_id(interaction.message.id)
            if not app:
                await interaction.response.send_message(components=[v2_msg("Заявление не найдено.")], ephemeral=True)
                return

            _, user_id, _, _, _, _, _, status, *_ = app
            if status != "pending":
                await interaction.response.send_message(components=[v2_msg(f"Заявление уже обработано (статус: {status}).")], ephemeral=True)
                return

            if member.id == user_id:
                await interaction.response.send_message(components=[v2_msg("Нельзя отклонять своё собственное заявление.")], ephemeral=True)
                return

            modal = ResignationRejectionReasonModal(app_data=app, interaction_message=interaction.message)
            await interaction.response.send_modal(modal)


class ResignationRejectionReasonModal(disnake.ui.Modal):
    def __init__(self, app_data, interaction_message):
        self.app_data = app_data
        self.interaction_message = interaction_message

        components = [
            disnake.ui.TextInput(
                label="Причина отклонения",
                custom_id="reason",
                placeholder="Укажите причину отклонения заявления на увольнение",
                required=True,
                max_length=500,
                style=disnake.TextInputStyle.paragraph,
            )
        ]
        super().__init__(title="Причина отклонения", components=components)

    async def callback(self, interaction: disnake.ModalInteraction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        app_id, user_id, user_name, nickname, static_id, rank, method, status, *_ = self.app_data
        reason_val = interaction.text_values["reason"].strip()

        update_application_status(app_id, "rejected", member.id, str(member))

        target = guild.get_member(user_id)
        if not target:
            try:
                target = await interaction.client.fetch_user(user_id)
            except Exception:
                target = interaction.user

        staff_title = get_staff_title(member, guild)

        container = build_resignation_container(
            app_id=app_id,
            user=target,
            nickname=nickname,
            static_id=static_id,
            rank=rank,
            reason="Собственное желание",
            status_text=f"Отклонено {staff_title}\nПричина отклонения: {reason_val}",
            guild=guild,
        )
        await self.interaction_message.edit(components=[container])

        logger.info(
            "ЗАЯВЛЕНИЕ НА УВОЛЬНЕНИЕ ОТКЛОНЕНО | Номер: #%s | Сотрудник: %s (%s) | Отклонил: %s (ID: %s) | Причина: %s",
            app_id, user_name, user_id, member, member.id, reason_val
        )

        dm_status = "ЛС закрыты"
        if target:
            desc_dm = (
                f"### Уведомление об увольнении #{app_id}\n\n"
                f"Ваше заявление на увольнение было **отклонено** {staff_title}.\n"
                f"> **Причина отклонения:** {reason_val}\n"
                f"> **Сервер:** {guild.name}"
            )
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay(desc_dm),
                accent_colour=disnake.Colour(0x2C2F33)
            )
            dm_sent = await send_dm(target, components=[dm_container])
            if dm_sent:
                dm_status = "ЛС отправлены"
        else:
            dm_status = "Пользователь покинул сервер"

        await interaction.followup.send(
            components=[v2_msg(f"Заявление на увольнение отклонено. Причина: {reason_val}\n{dm_status}")],
            ephemeral=True,
        )


class ApplicationsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

