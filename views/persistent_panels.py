import disnake
from disnake import ui

from config.settings import settings
from utils.helpers import can_manage_audit, v2_msg
from utils.interaction_guard import interaction_guard


class ApplicationPanelActions(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="Подать заявку на роль",
        style=disnake.ButtonStyle.primary,
        custom_id="panel:application:submit",
    )
    async def apply_button(self, button: ui.Button, interaction: disnake.MessageInteraction) -> None:
        remaining = interaction_guard.check_cooldown(
            interaction.user.id, "panel:application", settings.button_cooldown_seconds
        )
        if remaining is not None:
            await interaction.response.send_message(
                components=[v2_msg(f"Подождите {remaining:.1f} сек. перед повторным нажатием.")],
                ephemeral=True,
            )
            return

        from database import is_blacklisted
        from cogs.applications import ApplicationModal

        if is_blacklisted(interaction.user.id):
            await interaction.response.send_message(
                components=[v2_msg(" Вы внесены в Чёрный Список (ЧС) и не можете отправлять заявки!")],
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ApplicationModal(user_id=interaction.user.id))


class ResignationPanelActions(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="Подать заявление на увольнение",
        style=disnake.ButtonStyle.danger,
        custom_id="panel:resignation:submit",
    )
    async def resign_button(self, button: ui.Button, interaction: disnake.MessageInteraction) -> None:
        remaining = interaction_guard.check_cooldown(
            interaction.user.id, "panel:resignation", settings.button_cooldown_seconds
        )
        if remaining is not None:
            await interaction.response.send_message(
                components=[v2_msg(f"Подождите {remaining:.1f} сек. перед повторным нажатием.")],
                ephemeral=True,
            )
            return

        from database import get_user_latest_application
        from cogs.applications import ResignationModal

        user_data = get_user_latest_application(interaction.user.id)
        await interaction.response.send_modal(ResignationModal(user_data, user_id=interaction.user.id))


class AuditPanelActions(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="Отписать в кадровый аудит",
        style=disnake.ButtonStyle.primary,
        custom_id="panel:audit:open",
    )
    async def audit_button(self, button: ui.Button, interaction: disnake.MessageInteraction) -> None:
        if not can_manage_audit(interaction.user):
            await interaction.response.send_message(components=[v2_msg("У вас нет прав для кадрового аудита.")], ephemeral=True)
            return

        remaining = interaction_guard.check_cooldown(
            interaction.user.id, "panel:audit", settings.button_cooldown_seconds
        )
        if remaining is not None:
            await interaction.response.send_message(
                components=[v2_msg(f"Подождите {remaining:.1f} сек. перед повторным нажатием.")],
                ephemeral=True,
            )
            return

        from cogs.audit import AuditActionView

        view = AuditActionView()
        action_row = disnake.ui.ActionRow(*view.children)
        container = disnake.ui.Container(
            disnake.ui.TextDisplay("Выберите действие для кадрового аудита"),
            action_row,
            accent_colour=disnake.Colour(0x2C2F33)
        )
        await interaction.response.send_message(
            components=[container],
            ephemeral=True,
        )


PERSISTENT_VIEWS = (
    ApplicationPanelActions,
    ResignationPanelActions,
    AuditPanelActions,
)

PANEL_LAYOUTS = {
    "application": ApplicationPanelActions,
    "resignation": ResignationPanelActions,
    "audit": AuditPanelActions,
}

PANEL_TITLES = {
    "application": "Подача заявок на роли",
    "resignation": "Заявление на увольнение",
    "audit": "Кадровый аудит",
}
