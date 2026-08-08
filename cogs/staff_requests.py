import disnake
from disnake.ext import commands
import logging
import time
from config.constants import settings
from database import get_user, add_or_update_user, add_to_blacklist, add_audit_record, is_blacklisted, add_staff_request, get_staff_request_by_message_id, update_staff_request_status
from utils.helpers import v2_msg, can_manage_audit, parse_duration
logger = logging.getLogger(__name__)

_REQUEST_SESSION_TTL = 600  # 10 минут
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
def can_submit_request(member: disnake.Member) -> bool:
    starshina_idx = 4
    if starshina_idx >= len(settings.ranks):
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
        user_db = get_user(target.id)
        if not user_db:
            await interaction.response.send_message(components=[v2_msg("Пользователь не найден в БД.")], ephemeral=True)
            return
        _set_req_session(interaction.user.id, {
            "target_id": target.id,
            "target_name": target.display_name,
            "target_mention": target.mention,
            "old_rank": user_db.get("rank", "Нет звания"),
            "static_id": user_db.get("static_id", "Не указан")
        })
        await interaction.response.send_message(
            content="Выберите действие:",
            view=StaffRequestActionSelectView(),
            ephemeral=True
        )
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
        if action == "Fire":
            await interaction.response.send_modal(StaffRequestFireModal())
        else:
            current_rank = session["old_rank"]
            idx = settings.ranks.index(current_rank) if current_rank in settings.ranks else -1
            performer_rank_idx = -1
            for role in interaction.user.roles:
                if role.name in settings.ranks:
                    r_idx = settings.ranks.index(role.name)
                    if r_idx > performer_rank_idx:
                        performer_rank_idx = r_idx
            if performer_rank_idx == -1:
                performer_rank_idx = len(settings.ranks) - 1
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
            await interaction.response.send_message(
                content="Выберите новое звание:",
                view=StaffRequestRankSelectView(valid_ranks),
                ephemeral=True
            )
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
    performer_mention: str = ""
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
        performer_mention=interaction.user.mention
    )
    msg = await channel.send(components=[container])
    
    add_staff_request(
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
        
        req = get_staff_request_by_message_id(interaction.message.id)
        if not req:
            await interaction.followup.send("Рапорт не найден в базе данных.", ephemeral=True)
            return
        if req["status"] != "pending":
            await interaction.followup.send(f"Рапорт уже обработан (статус: {req['status']}).", ephemeral=True)
            return
            
        target_id = req["target_id"]
        target = interaction.guild.get_member(target_id)
        if not target:
            await interaction.followup.send("Сотрудник уже покинул сервер.", ephemeral=True)
            return
            
        from utils.helpers import sync_user_roles_and_nickname
        import asyncio
        from utils.roster_generator import update_cpps_roster
        
        user_db = get_user(target_id)
        static_id = user_db.get("static_id", "Не указан") if user_db else "Не указан"
        old_rank = user_db.get("rank", "") if user_db else req["old_rank"]
        
        action = req["action"]
        reason = req["reason"]
        target_mention = req["target_mention"]
        new_rank = req["new_rank"]
        want_bl = req["want_bl"]
        bl_duration = req["bl_duration"]
        
        if action == "Fire":
            fired_role = interaction.guild.get_role(settings.fired_role_id)
            roles_to_remove = []
            for r in target.roles:
                if r.id in settings.protected_role_ids or r.name == "@everyone":
                    continue
                roles_to_remove.append(r)
            errors = []
            try:
                await target.remove_roles(*roles_to_remove)
                if fired_role: await target.add_roles(fired_role)
                await target.edit(nick=None)
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
                removed_roles=", ".join(r.name for r in roles_to_remove)
            )
            
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], static_id, old_rank, "fired")
                
            if want_bl:
                duration_display = bl_duration or "Навсегда"
                expires_at = None
                if bl_duration:
                    dt = parse_duration(bl_duration)
                    expires_at = dt.isoformat() if dt else None
                user_db_bl = get_user(target_id)
                nickname_bl = user_db_bl["nickname"] if user_db_bl else str(target)
                static_id_bl = user_db_bl["static_id"] if user_db_bl else "Не указан"
                add_to_blacklist(
                    user_id=target_id,
                    nickname=nickname_bl,
                    static_id=static_id_bl,
                    reason=reason,
                    added_by_id=interaction.user.id,
                    added_by_name=str(interaction.user),
                    expires_at=expires_at,
                )
                if settings.blacklist_channel_id:
                    bl_ch = interaction.guild.get_channel(settings.blacklist_channel_id)
                    if bl_ch:
                        pings = " ".join([f"<@&{r}>" for r in settings.blacklist_ping_roles])
                        bl_embed = disnake.Embed(title="Занесение в ЧС (Рапорт)", color=disnake.Color.red())
                        bl_embed.add_field(name="Сотрудник", value=target.mention, inline=False)
                        bl_embed.add_field(name="Причина", value=reason, inline=False)
                        bl_embed.add_field(name="Срок", value=duration_display, inline=False)
                        bl_embed.add_field(name="Инициатор", value=interaction.user.mention, inline=False)
                        try:
                            kwargs = {"embed": bl_embed}
                            if pings:
                                kwargs["content"] = pings
                            await bl_ch.send(**kwargs)
                        except Exception as e:
                            logger.error(f"Не удалось отправить уведомление в ЧС: {e}")
                            
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
                status_text=f"✅ Одобрено {interaction.user.mention}",
                action_row=None,
                performer_mention=""
            )
            await interaction.message.edit(components=[container])
            await interaction.followup.send(components=[v2_msg("Сотрудник уволен.")], ephemeral=True)
            
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
            
            if user_db:
                add_or_update_user(target_id, user_db["nickname"], static_id, new_rank, "active")

            # Logging to battalion assignment channel
            assigned_dept = None
            for dept_name, role_id in settings.department_role_ids.items():
                r = interaction.guild.get_role(role_id)
                if r and r in target.roles:
                    assigned_dept = dept_name
                    break
            
            if assigned_dept and "батальон" in assigned_dept.lower():
                com_1, dep_1 = settings.cmdr_1_role_id, settings.dep_cmdr_1_role_id
                com_2, dep_2 = settings.cmdr_2_role_id, settings.dep_cmdr_2_role_id
                com_3, dep_3 = settings.cmdr_3_role_id, settings.dep_cmdr_3_role_id
                pings = ""
                if "1-й" in assigned_dept: pings = f"<@&{com_1}> <@&{dep_1}>"
                elif "2-й" in assigned_dept: pings = f"<@&{com_2}> <@&{dep_2}>"
                elif "3-й" in assigned_dept: pings = f"<@&{com_3}> <@&{dep_3}>"
                
                if pings:
                    dept_embed = disnake.Embed(
                        title="Зачисление в батальон (По Рапорту)",
                        color=disnake.Color(0x2C2F33)
                    )
                    dept_embed.add_field(name="Сотрудник", value=target.mention, inline=False)
                    dept_embed.add_field(name="Статик", value=static_id, inline=False)
                    dept_embed.add_field(name="Отдел", value=assigned_dept, inline=False)
                    try:
                        if settings.battalion_assignment_channel_id:
                            ch = interaction.guild.get_channel(settings.battalion_assignment_channel_id)
                            if ch:
                                await ch.send(content=pings, embed=dept_embed)
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
                status_text=f"✅ Одобрено {interaction.user.mention}",
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
        
        req = get_staff_request_by_message_id(interaction.message.id)
        if not req:
            await interaction.followup.send("Рапорт не найден в базе данных.", ephemeral=True)
            return
        if req["status"] != "pending":
            await interaction.followup.send(f"Рапорт уже обработан (статус: {req['status']}).", ephemeral=True)
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
            status_text=f"❌ Отклонено {interaction.user.mention}",
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
    # Note: StaffRequestRankSelectView requires valid_ranks, so we can't register it globally without arguments.
    # However, since it is sent ephemerally during an active interaction flow, Discord will track it until restart.
    # To fully prevent timeout on Rank select, we would need persistent storage or custom id parsing,
    # but the primary timeout is usually on UserSelect or ActionSelect.
    bot.add_view(StaffRequestApprovalView())