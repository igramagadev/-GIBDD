import disnake
from disnake.ext import commands
import logging
import time
from config.constants import settings
from database import get_user, add_or_update_user, add_to_blacklist
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
        await _submit_request(interaction, self.text_values["reason"].strip(), None, None)
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
        reason = self.text_values["reason"].strip()
        bl_decision = self.text_values["bl_decision"].strip().lower()
        bl_duration = self.text_values.get("bl_duration", "").strip()
        want_bl = False
        if bl_decision in ("да", "yes", "+", "y", "д", "da"):
            want_bl = True
        await _submit_request(interaction, reason, want_bl, bl_duration)
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
    old_rank = session["old_rank"]
    if action == "Fire":
        title = "Рапорт на увольнение"
        desc = f"**Сотрудник:** {target_mention}\n**Причина:** {reason}\n**ЧС:** {'Да' if want_bl else 'Нет'}"
        if want_bl and bl_duration:
            desc += f" (Срок: {bl_duration})"
    else:
        new_rank = session.get("new_rank", "Неизвестно")
        action_ru = "Повышение" if action == "Promote" else "Понижение"
        title = f"Рапорт на {action_ru.lower()}"
        desc = f"**Сотрудник:** {target_mention}\n**Изменение:** {old_rank} ➔ {new_rank}\n**Причина:** {reason}"
    embed = disnake.Embed(title=title, description=desc, color=disnake.Color(0x2C2F33))
    embed.add_field(name="Подал рапорт", value=interaction.user.mention, inline=False)
    view = StaffRequestApprovalView()
    msg = await channel.send(embed=embed, view=view)
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
        embed = interaction.message.embeds[0]
        desc = embed.description
        import re
        target_match = re.search(r'<@!?(\d+)>', desc)
        if not target_match:
            await interaction.followup.send("Не удалось найти ID сотрудника в рапорте.", ephemeral=True)
            return
        target_id = int(target_match.group(1))
        target = interaction.guild.get_member(target_id)
        if not target:
            await interaction.followup.send("Сотрудник уже покинул сервер.", ephemeral=True)
            return
        from database import get_user, add_or_update_user, add_audit_record, is_blacklisted, add_blacklist
        from utils.helpers import sync_user_roles_and_nickname
        import asyncio
        from utils.roster_generator import update_cpps_roster
        user_db = get_user(target_id)
        static_id = user_db.get("static_id", "Не указан") if user_db else "Не указан"
        old_rank = user_db.get("rank", "") if user_db else ""
        reason_match = re.search(r'\*\*Причина:\*\*\s*(.*)', desc)
        reason = reason_match.group(1).strip() if reason_match else "По рапорту командира"
        title = embed.title.lower()
        if "увольнение" in title:
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
            if "ЧС: Да" in desc:
                dur_match = re.search(r'\(Срок:\s*(.*?)\)', desc)
                duration_str = dur_match.group(1).strip() if dur_match else None
                duration_display = duration_str or "Навсегда"
                expires_at = None
                if duration_str:
                    dt = parse_duration(duration_str)
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
                        bl_embed = disnake.Embed(title="Занесение в ЧС", color=disnake.Color.red())
                        bl_embed.add_field(name="Сотрудник", value=target.mention, inline=False)
                        bl_embed.add_field(name="Причина", value=reason, inline=False)
                        bl_embed.add_field(name="Срок", value=duration_display, inline=False)
                        bl_embed.add_field(name="Инициатор", value=interaction.user.mention, inline=False)
                        await bl_ch.send(content=pings, embed=bl_embed)
            asyncio.create_task(update_cpps_roster(interaction.guild))
            embed.color = disnake.Color.green()
            embed.add_field(name="Статус", value=f"✅ Одобрено {interaction.user.mention}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.followup.send("Сотрудник уволен.", ephemeral=True)
        else:
            action = "Promote" if "повышение" in title else "Demote"
            audit_action = "Повысить" if action == "Promote" else "Понизить"
            rank_match = re.search(r'\*\*Изменение:\*\*\s*(.*?)\s*➔\s*(.*)', desc)
            if not rank_match:
                await interaction.followup.send("Не удалось определить новое звание.", ephemeral=True)
                return
            new_rank = rank_match.group(2).strip()
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
            asyncio.create_task(update_cpps_roster(interaction.guild))
            embed.color = disnake.Color.green()
            embed.add_field(name="Статус", value=f"✅ Одобрено {interaction.user.mention}")
            await interaction.message.edit(embed=embed, view=None)
            await interaction.followup.send("Сотрудник обновлён.", ephemeral=True)
    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="req:deny")
    async def deny(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message("У вас нет прав отклонять рапорты.", ephemeral=True)
            return
        embed = interaction.message.embeds[0]
        embed.color = disnake.Color.red()
        embed.add_field(name="Статус", value=f"❌ Отклонено {interaction.user.mention}")
        await interaction.response.edit_message(embed=embed, view=None)
class StaffRequestsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def init_panel(self):
        from utils.panel_init import send_v2_panel
        await send_v2_panel(self.bot, settings.staff_requests_panel_channel_id, "staff_request")

def setup(bot: commands.Bot):
    bot.add_cog(StaffRequestsCog(bot))
    bot.add_view(StaffRequestApprovalView())