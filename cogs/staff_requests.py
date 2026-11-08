import disnake
from disnake.ext import commands
import logging
import time
from config.constants import settings
from database import get_user, add_or_update_user, add_to_blacklist, add_audit_record, is_blacklisted, add_staff_request, get_staff_request_by_message_id, claim_staff_request, update_staff_request_status
from utils.helpers import (
    v2_msg,
    can_manage_audit,
    can_manage_role,
    clean_role_name,
    get_member_rank_index,
    is_protected_role,
    member_department,
    parse_duration,
    separate_battalion_pings,
    employee_refusal,
    promotion_cooldown_notice,
)
logger = logging.getLogger(__name__)

_REQUEST_SESSION_TTL = 600  
_request_sessions: dict[int, dict] = {}

def _cleanup_request_sessions() -> None:
    now = time.monotonic()
    expired = [
        uid for uid, data in _request_sessions.items()
        if now - data.get("_ts", 0) > _REQUEST_SESSION_TTL
    ]
    for uid in expired:
        del _request_sessions[uid]

def _get_req_session(user_id: int) -> dict | None:
    session = _request_sessions.get(user_id)
    if not session:
        return None
    if time.monotonic() - session.get("_ts", 0) > _REQUEST_SESSION_TTL:
        _request_sessions.pop(user_id, None)
        return None
    return session

def _set_req_session(user_id: int, data: dict):
    _cleanup_request_sessions()
    data["_ts"] = time.monotonic()
    _request_sessions[user_id] = data
async def _ephemeral_v2(interaction: disnake.Interaction, text: str, view: disnake.ui.View) -> None:
    container = disnake.ui.Container(
        disnake.ui.TextDisplay(text),
        disnake.ui.ActionRow(*view.children),
        accent_colour=disnake.Colour(0x2C2F33),
    )
    await interaction.response.send_message(components=[container], ephemeral=True)
    msg = await interaction.original_message()
    interaction.bot._connection.store_view(view, msg.id)

def can_submit_request(member: disnake.Member) -> bool:
    try:
        starshina_idx = settings.ranks.index("Старшина")
    except ValueError:
        return False
    allowed_role_ids = set()
    for rank_name in settings.ranks[starshina_idx:]:
        role_id = settings.ranks_map.get(rank_name)
        if role_id:
            allowed_role_ids.add(role_id)
    for role in member.roles:
        if role.id in allowed_role_ids:
            return True
    return False
class StaffRequestUserSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @disnake.ui.user_select(placeholder="Выберите сотрудника...", custom_id="req:select_user")
    async def select_user(self, select: disnake.ui.UserSelect, interaction: disnake.MessageInteraction):
        target = select.values[0]
        if interaction.user.id == target.id:
            await interaction.response.send_message(components=[v2_msg("Нельзя писать рапорт на самого себя.")], ephemeral=True)
            return
        member = target if isinstance(target, disnake.Member) else interaction.guild.get_member(target.id)
        user_db = get_user(target.id)
        refusal = employee_refusal(member, user_db, self_target=False)
        if refusal:
            await interaction.response.send_message(components=[v2_msg(refusal)], ephemeral=True)
            return
        _set_req_session(interaction.user.id, {
            "target_id": target.id,
            "target_name": target.display_name,
            "target_mention": target.mention,
            "old_rank": user_db.get("rank", "Нет звания"),
            "static_id": user_db.get("static_id", "Не указан")
        })
        await _ephemeral_v2(interaction, "Выберите действие:", StaffRequestActionSelectView())
class StaffRequestActionSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [
            disnake.SelectOption(label="Повысить", value="Promote"),
            disnake.SelectOption(label="Понизить", value="Demote"),
            disnake.SelectOption(label="Уволить", value="Fire")
        ]
        self.select_action = disnake.ui.StringSelect(
            placeholder="Что сделать с сотрудником?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="req:select_action"
        )
        self.select_action.callback = self.action_callback
        self.add_item(self.select_action)
    async def action_callback(self, interaction: disnake.MessageInteraction):
        action = self.select_action.values[0]
        session = _get_req_session(interaction.user.id)
        if not session:
            await interaction.response.send_message(components=[v2_msg("Сессия истекла.")], ephemeral=True)
            return
        session["action"] = action
        target = interaction.guild.get_member(session["target_id"]) if interaction.guild else None
        refusal = employee_refusal(target, get_user(session["target_id"]), self_target=False)
        if refusal:
            await interaction.response.send_message(components=[v2_msg(refusal)], ephemeral=True)
            return
        if action == "Fire":
            await interaction.response.send_modal(StaffRequestFireModal())
        else:
            current_rank = session["old_rank"]
            idx = settings.ranks.index(current_rank) if current_rank in settings.ranks else -1
            performer_rank_idx = get_member_rank_index(interaction.user, interaction.guild)
            if performer_rank_idx == -1:
                await interaction.response.send_message(
                    components=[v2_msg("Не удалось определить ваше звание. Рапорт на изменение звания недоступен.")],
                    ephemeral=True,
                )
                return
            if action == "Demote":
                if idx <= 0:
                    await interaction.response.send_message(components=[v2_msg(f"Сотрудник имеет звание '{current_rank}', понижать некуда.")], ephemeral=True)
                    return
                valid_ranks = settings.ranks[:idx]
            else: 
                if idx >= performer_rank_idx:
                    await interaction.response.send_message(components=[v2_msg(f"Вы не можете повысить сотрудника, его звание ({current_rank}) выше или равно вашему.")], ephemeral=True)
                    return
                valid_ranks = settings.ranks[idx+1:performer_rank_idx+1]
            if not valid_ranks:
                await interaction.response.send_message(components=[v2_msg("Нет доступных званий.")], ephemeral=True)
                return
            prompt = "### Выберите новое звание"
            if action == "Promote":
                notice = promotion_cooldown_notice(session["target_id"], current_rank)
                if notice:
                    prompt = f"{prompt}\n\n{notice}"
            await _ephemeral_v2(interaction, prompt, StaffRequestRankSelectView(valid_ranks))
class StaffRequestRankSelectView(disnake.ui.View):
    def __init__(self, valid_ranks: list[str]):
        super().__init__(timeout=None)
        options = [disnake.SelectOption(label=rank, value=rank) for rank in valid_ranks]
        self.select_rank = disnake.ui.StringSelect(
            placeholder="Выберите звание...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="req:select_rank"
        )
        self.select_rank.callback = self.rank_callback
        self.add_item(self.select_rank)
    async def rank_callback(self, interaction: disnake.MessageInteraction):
        new_rank = self.select_rank.values[0]
        session = _get_req_session(interaction.user.id)
        if session:
            session["new_rank"] = new_rank
        await interaction.response.send_modal(StaffRequestReasonModal())
class StaffRequestReasonModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Причина / Рапорт",
                custom_id="reason",
                required=True,
                max_length=500,
                style=disnake.TextInputStyle.paragraph
            )
        ]
        super().__init__(title="Рапорт (Повышение/Понижение)", components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        await _submit_request(interaction, interaction.text_values["reason"].strip(), None, None)
class StaffRequestFireModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Причина увольнения / Рапорт",
                custom_id="reason",
                required=True,
                max_length=500,
                style=disnake.TextInputStyle.paragraph
            ),
            disnake.ui.TextInput(
                label="Занести в ЧС? (Да / Нет)",
                custom_id="bl_decision",
                required=True,
                max_length=10
            ),
            disnake.ui.TextInput(
                label="Срок ЧС (если Да)",
                custom_id="bl_duration",
                required=False,
                placeholder="Пример: 30 дней, Навсегда",
                max_length=50
            )
        ]
        super().__init__(title="Рапорт (Увольнение)", components=components)
    async def callback(self, interaction: disnake.ModalInteraction):
        reason = interaction.text_values["reason"].strip()
        bl_decision = interaction.text_values["bl_decision"].strip().lower()
        bl_duration = interaction.text_values.get("bl_duration", "").strip()
        want_bl = False
        if bl_decision in ("да", "yes", "+", "y", "д", "da"):
            want_bl = True
        await _submit_request(interaction, reason, want_bl, bl_duration)
def build_request_container(
    target_mention: str,
    action: str,
    reason: str,
    old_rank: str,
    new_rank: str,
    want_bl: bool,
    bl_duration: str,
    status_text: str,
    action_row: disnake.ui.ActionRow = None,
    performer_mention: str = "",
    notice: str = "",
) -> disnake.ui.Container:
    if action == "Fire":
        title = "Рапорт на увольнение"
        desc = f"**Сотрудник:** {target_mention}\n**Причина:** {reason}\n**ЧС:** {'Да' if want_bl else 'Нет'}"
        if want_bl and bl_duration:
            desc += f" (Срок: {bl_duration})"
    else:
        action_ru = "Повышение" if action == "Promote" else "Понижение"
        title = f"Рапорт на {action_ru.lower()}"
        desc = f"**Сотрудник:** {target_mention}\n**Изменение:** {old_rank} ➔ {new_rank}\n**Причина:** {reason}"
    
    components = [
        disnake.ui.TextDisplay(f"### {title}"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(desc)
    ]
    if performer_mention:
        components.append(disnake.ui.TextDisplay(f"**Подал рапорт:** {performer_mention}"))
    if notice:
        components.append(disnake.ui.Separator())
        components.append(disnake.ui.TextDisplay(notice))
    components.append(disnake.ui.Separator())
    components.append(disnake.ui.TextDisplay(f"**Статус:** {status_text}"))
    if action_row:
        components.append(action_row)
    return disnake.ui.Container(*components, accent_colour=disnake.Colour(0x2C2F33))

async def _submit_request(interaction: disnake.ModalInteraction, reason: str, want_bl: bool, bl_duration: str):
    session = _get_req_session(interaction.user.id)
    if not session:
        await interaction.response.send_message(components=[v2_msg("Сессия истекла.")], ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel_id = settings.staff_requests_channel_id
    if not channel_id:
        await interaction.followup.send("Канал для рапортов не настроен (STAFF_REQUESTS_CHANNEL_ID).", ephemeral=True)
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("Канал для рапортов не найден на сервере.", ephemeral=True)
        return
    action = session["action"]
    target_mention = session["target_mention"]
    target_id = session["target_id"]
    old_rank = session["old_rank"]
    new_rank = session.get("new_rank", "")
    
    view = StaffRequestApprovalView()
    action_row = disnake.ui.ActionRow(*view.children)
    notice = ""
    if action == "Promote":
        notice = promotion_cooldown_notice(target_id, old_rank)
    
    container = build_request_container(
        target_mention=target_mention,
        action=action,
        reason=reason,
        old_rank=old_rank,
        new_rank=new_rank,
        want_bl=want_bl,
        bl_duration=bl_duration,
        status_text="Ожидает рассмотрения",
        action_row=action_row,
        performer_mention=interaction.user.mention,
        notice=notice,
    )
    msg = await channel.send(components=[container])
    
    saved = add_staff_request(
        message_id=msg.id,
        target_id=target_id,
        target_mention=target_mention,
        action=action,
        reason=reason,
        old_rank=old_rank,
        new_rank=new_rank,
        want_bl=want_bl,
        bl_duration=bl_duration
    )
    if not saved:
        await interaction.followup.send(
            components=[v2_msg("Рапорт отправлен в канал, но не сохранился в базе. Сообщите администратору.")],
            ephemeral=True,
        )
        return
    
    await interaction.followup.send(components=[v2_msg("Ваш рапорт успешно отправлен на рассмотрение старшему составу.")], ephemeral=True)

class StaffRequestApprovalView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Одобрить", style=disnake.ButtonStyle.success, custom_id="req:approve")
    async def approve(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message("У вас нет прав одобрять рапорты.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        req = claim_staff_request(interaction.message.id)
        if not req:
            existing = get_staff_request_by_message_id(interaction.message.id)
            if not existing:
                await interaction.followup.send("Рапорт не найден в базе данных.", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"Рапорт уже обработан (статус: {existing['status']}).",
                    ephemeral=True,
                )
            return
            
        target_id = req["target_id"]
        target = interaction.guild.get_member(target_id)
        if not target:
            update_staff_request_status(interaction.message.id, "pending")
            await interaction.followup.send("Сотрудник уже покинул сервер.", ephemeral=True)
            return
            
        from utils.helpers import sync_user_roles_and_nickname
        import asyncio
        from utils.roster_generator import update_cpps_roster
        
        user_db = get_user(target_id)
        refusal = employee_refusal(target, user_db, self_target=False)
        if refusal:
            update_staff_request_status(interaction.message.id, "pending")
            await interaction.followup.send(components=[v2_msg(refusal)], ephemeral=True)
            return
        static_id = user_db.get("static_id", "Не указан") if user_db else "Не указан"
        old_rank = user_db.get("rank", "") if user_db else req["old_rank"]
        
        action = req["action"]
        reason = req["reason"]
        target_mention = req["target_mention"]
        new_rank = req["new_rank"]
        want_bl = req["want_bl"]
        bl_duration = req["bl_duration"]
        
        if action == "Fire":
            bot_member = interaction.guild.get_member(interaction.client.user.id)
            fired_role = interaction.guild.get_role(settings.fired_role_id)
            errors = []
            removed_names = []
            cleanup_ids = set(settings.roles_to_cleanup_ids)
            cleanup_ids.update(settings.department_role_ids.values())
            cleanup_ids.update(settings.obsolete_department_role_ids)
            cleanup_ids.update(settings.ranks_map.values())
            cleanup_ids.update(settings.position_role_ids.values())
            for divider_id in (
                settings.divider_position_id,
                settings.divider_department_id,
                settings.divider_rank_id,
                settings.divider_access_id,
                settings.base_role_id,
                settings.cadet_role_id,
            ):
                if divider_id:
                    cleanup_ids.add(divider_id)
            cleanup_names = set(settings.roles_to_cleanup_names)
            for role in list(target.roles):
                if role.name == "@everyone" or is_protected_role(role):
                    continue
                should_remove = role.id in cleanup_ids or role.name in cleanup_names
                if should_remove and can_manage_role(bot_member, role):
                    try:
                        await target.remove_roles(role)
                        removed_names.append(clean_role_name(role.name))
                    except Exception as e:
                        errors.append(str(e))
            try:
                if fired_role and can_manage_role(bot_member, fired_role):
                    await target.add_roles(fired_role)
                base_name = target.display_name
                if " | " in base_name:
                    base_name = base_name.split(" | ")[-1].strip()
                fired_nick = f"Уволен | {base_name}"
                if len(fired_nick) > 32:
                    fired_nick = f"Уволен | {base_name[:(32-len('Уволен | '))]}"
                await target.edit(nick=fired_nick)
            except Exception as e:
                errors.append(str(e))
                
            add_audit_record(
                action="Уволить",
                target_user_id=target_id,
                target_user_name=str(target),
                target_static_id=static_id,
                target_rank=old_rank,
                target_position="",
                method="Рапорт командира",
                reason=reason,
                performed_by_id=interaction.user.id,
                performed_by_name=str(interaction.user),
                issued_roles="Уволен",
                removed_roles=", ".join(removed_names) if removed_names else "Нет"
            )
            
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], static_id, old_rank, "fired")
                
            if want_bl:
                from utils.helpers import give_blacklist
                user_db_bl = get_user(target_id)
                nickname_bl = user_db_bl["nickname"] if user_db_bl else target.display_name
                static_id_bl = user_db_bl["static_id"] if user_db_bl else static_id
                errors.extend(await give_blacklist(
                    interaction.guild,
                    target,
                    user_id=target_id,
                    nickname=nickname_bl,
                    static_id=static_id_bl,
                    reason=reason,
                    duration_str=bl_duration or "",
                    added_by=interaction.user,
                ))
                            
            asyncio.create_task(update_cpps_roster(interaction.guild))
            update_staff_request_status(interaction.message.id, "approved")
            
            container = build_request_container(
                target_mention=target_mention,
                action=action,
                reason=reason,
                old_rank=old_rank,
                new_rank=new_rank,
                want_bl=want_bl,
                bl_duration=bl_duration,
                status_text=f"Одобрено {interaction.user.mention}",
                action_row=None,
                performer_mention=""
            )
            await interaction.message.edit(components=[container])
            done_text = "Сотрудник уволен."
            if errors:
                done_text += "\nОшибки: " + ", ".join(errors)
            await interaction.followup.send(components=[v2_msg(done_text)], ephemeral=True)
            
        else:
            audit_action = "Повысить" if action == "Promote" else "Понизить"
            bot_member = interaction.guild.get_member(interaction.client.user.id)
            issued_roles, removed_roles, errors = await sync_user_roles_and_nickname(target, interaction.guild, new_rank, bot_member)
            
            add_audit_record(
                action=audit_action,
                target_user_id=target_id,
                target_user_name=str(target),
                target_static_id=static_id,
                target_rank=new_rank,
                target_position="",
                method="Рапорт командира",
                reason=reason,
                performed_by_id=interaction.user.id,
                performed_by_name=str(interaction.user),
                issued_roles=", ".join(issued_roles),
                removed_roles=", ".join(removed_roles)
            )
            from cogs.audit import build_audit_container, post_audit_container
            action_verb = "повышает" if action == "Promote" else "понижает"
            await post_audit_container(
                interaction.guild,
                build_audit_container(
                    action_verb,
                    interaction.user,
                    target,
                    static_id,
                    old_rank=old_rank,
                    new_rank=new_rank,
                    reason=reason,
                    issued_roles=", ".join(issued_roles) if issued_roles else None,
                    removed_roles=", ".join(removed_roles) if removed_roles else None,
                ),
            )
            
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], static_id, new_rank, "active")

            assigned_dept = member_department(target, interaction.guild)
            pings = separate_battalion_pings(assigned_dept)
            if pings:
                assign_text = (
                    f"{pings}\n"
                    f"**Зачисление в 1-й Отдельный Батальон — По Рапорту**\n\n"
                    f"**Сотрудник:** {target.mention}\n"
                    f"**Статик:** {static_id}\n"
                    f"**Отдел:** {assigned_dept}"
                )
                try:
                    if settings.battalion_assignment_channel_id:
                        ch = interaction.guild.get_channel(settings.battalion_assignment_channel_id)
                        if ch is None:
                            ch = await interaction.guild.fetch_channel(settings.battalion_assignment_channel_id)
                        if ch:
                            from utils.helpers import send_notice
                            await send_notice(ch, assign_text)
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление о зачислении: {e}")

            asyncio.create_task(update_cpps_roster(interaction.guild))
            update_staff_request_status(interaction.message.id, "approved")
            
            container = build_request_container(
                target_mention=target_mention,
                action=action,
                reason=reason,
                old_rank=old_rank,
                new_rank=new_rank,
                want_bl=want_bl,
                bl_duration=bl_duration,
                status_text=f"Одобрено {interaction.user.mention}",
                action_row=None,
                performer_mention=""
            )
            await interaction.message.edit(components=[container])
            await interaction.followup.send(components=[v2_msg("Сотрудник обновлён.")], ephemeral=True)

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="req:deny")
    async def deny(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message("У вас нет прав отклонять рапорты.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        
        req = claim_staff_request(interaction.message.id)
        if not req:
            existing = get_staff_request_by_message_id(interaction.message.id)
            if not existing:
                await interaction.followup.send("Рапорт не найден в базе данных.", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"Рапорт уже обработан (статус: {existing['status']}).",
                    ephemeral=True,
                )
            return
            
        update_staff_request_status(interaction.message.id, "rejected")
        container = build_request_container(
            target_mention=req["target_mention"],
            action=req["action"],
            reason=req["reason"],
            old_rank=req["old_rank"],
            new_rank=req["new_rank"],
            want_bl=req["want_bl"],
            bl_duration=req["bl_duration"],
            status_text=f"Отклонено {interaction.user.mention}",
            action_row=None,
            performer_mention=""
        )
        await interaction.message.edit(components=[container])
        await interaction.followup.send(components=[v2_msg("Рапорт отклонён.")], ephemeral=True)
class StaffRequestsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def init_panel(self):
        from utils.panel_init import send_v2_panel
        await send_v2_panel(self.bot, settings.staff_requests_panel_channel_id, "staff_request")

def setup(bot: commands.Bot):
    bot.add_cog(StaffRequestsCog(bot))
    bot.add_view(StaffRequestUserSelectView())
    bot.add_view(StaffRequestActionSelectView())
    bot.add_view(StaffRequestApprovalView())
