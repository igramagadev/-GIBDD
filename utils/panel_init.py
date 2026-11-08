import logging
import disnake
from views.persistent_panels import PANEL_LAYOUTS
logger = logging.getLogger("bot.panels")
async def send_v2_panel(
    bot: disnake.Client,
    channel_id: int,
    panel_key: str,
    *,
    history_limit: int = 50,
) -> bool:
    layout_cls = PANEL_LAYOUTS.get(panel_key)
    if not layout_cls:
        logger.error("Неизвестный ключ панели: %s", panel_key)
        return False
    for guild in bot.guilds:
        channel = guild.get_channel(channel_id)
        if not channel:
            logger.warning("[%s] Канал %s не найден на сервере %s", panel_key, channel_id, guild.name)
            continue
        panel_custom_ids = {
            "application": "panel:application:submit",
            "resignation": "panel:resignation:submit",
            "audit": "panel:audit:open",
            "staff_request": "panel:staff_request:submit",
        }
        target_custom_id = panel_custom_ids.get(panel_key, "")
        deleted = 0
        async for message in channel.history(limit=history_limit):
            if message.author.id != bot.user.id:
                continue
            def _find_custom_id(comps, target):
                for c in comps:
                    if getattr(c, "custom_id", None) == target:
                        return True
                    if _find_custom_id(getattr(c, "children", []), target):
                        return True
                    if _find_custom_id(getattr(c, "components", []), target):
                        return True
                    if hasattr(c, "to_dict"):
                        try:
                            if target in str(c.to_dict()):
                                return True
                        except Exception:
                            pass
                return False
            is_old_panel = _find_custom_id(message.components, target_custom_id)
            if is_old_panel:
                try:
                    await message.delete()
                    deleted += 1
                except disnake.HTTPException as exc:
                    logger.warning("[%s] Не удалось удалить сообщение %s: %s", panel_key, message.id, exc)
        try:
            view = layout_cls()
            if panel_key == "application":
                desc_display = disnake.ui.TextDisplay(
                    "# Электронная приемная УГИБДД\n\n"
                    "Нажмите кнопку ниже, чтобы открыть форму подачи заявления.\n\n"
                    "**Для сотрудников:**\n"
                    "• Игровой никнейм\n"
                    "• Static ID\n"
                    "• Звание\n\n"
                    "**Для других организаций:**\n"
                    "• Игровой никнейм\n"
                    "• Static ID\n"
                    "• Кто вы / Откуда\n"
                    "• Доказательства"
                )
            elif panel_key == "resignation":
                desc_display = disnake.ui.TextDisplay(
                    "# Расторжение контракта\n\n"
                    "Нажмите кнопку ниже, чтобы открыть форму расторжения контракта по собственному желанию.\n\n"
                    "**Требуемые данные:**\n"
                    "• Игровой никнейм\n"
                    "• Static ID\n"
                    "• Текущее звание\n"
                    "• Причина увольнения"
                )
            elif panel_key == "audit":
                desc_display = disnake.ui.TextDisplay(
                    "# Кадровый аудит\nУправление личным составом\n\n"
                    "Интерфейс для внесения и фиксации изменений в штате сотрудников.\n\n"
                    "**Действия:**\n"
                    "• **Принять** — оформление нового сотрудника\n"
                    "• **Уволить** — расторжение контракта\n"
                    "• **Повысить / Понизить** — изменение звания\n"
                    "• **Перевод** — перевод сотрудника в другой отдел\n"
                )
            elif panel_key == "staff_request":
                desc_display = disnake.ui.TextDisplay(
                    "# Списки повышения\nПодача рапортов на сотрудников\n\n"
                    "Интерфейс для подачи рапортов на изменение личного состава.\n\n"
                    "**Доступные действия:**\n"
                    "• **Повысить** \u2014 рапорт на повышение сотрудника\n"
                    "• **Понизить** \u2014 рапорт на понижение сотрудника\n"
                    "• **Уволить** \u2014 рапорт на увольнение сотрудника\n\n"
                    "**Доступно от звания Старшина и выше.**"
                )
            else:
                logger.error("Неизвестный ключ панели в блоке генерации: %s", panel_key)
                return False
            action_row = disnake.ui.ActionRow(*view.children)
            container = disnake.ui.Container(
                desc_display,
                action_row,
                accent_colour=disnake.Colour(0x2C2F33)
            )
            await channel.send(components=[container])
            logger.info("[%s] Панель создана в #%s (удалено старых: %d)", panel_key, channel.name, deleted)
        except disnake.HTTPException as exc:
            logger.error("[%s] Ошибка создания панели: %s", panel_key, exc)
            return False
    return True