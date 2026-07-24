import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

def _int_env(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Переменная {name} должна быть числом, получено: {raw!r}") from exc

def _list_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]

def _int_list_env(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError(f"Переменная {name} должна содержать список чисел, получено: {raw!r}") from exc

def _dict_env(name: str, default: dict[str, str]) -> dict[str, str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    result = {}
    for item in raw.split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            result[k.strip()] = v.strip()
    return result

def _str_int_dict_env(name: str, default: dict[str, int]) -> dict[str, int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    result = {}
    for item in raw.split(","):
        if ":" in item:
            k, v = item.split(":", 1)
            try:
                result[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return result

_DEFAULT_ROLES_TO_CLEANUP = [
    "Рядовой", "Ефрейтор", "Младший сержант", "Сержант", "Старший сержант",
    "Старшина", "Прапорщик", "Старший прапорщик", "Младший лейтенант", "Лейтенант",
    "Старший лейтенант", "Капитан", "Майор", "Подполковник", "Полковник",
    "Генерал-майор", "Генерал-лейтенант", "Генерал-полковник",
    "Начальник УГИБДД", "Зам. Начальника УГИБДД",
    "Начальник ОСБ", "Зам. Начальника ОСБ",
    "Начальник ЦППС", "Зам. Начальника ЦППС",
    "Командир БМТО", "Зам. Командира БМТО",
    "Командир 1-го СБ", "Зам. Командира 1-го СБ",
    "Командир Полка", "Зам. Командира Полка",
    "Командир 1-го Батальона", "Командир 2-го Батальона", "Командир 3-го Батальона",
    "Отпуск", "КМБ",
    "Курс молодого бойца", "Курс повышения квалификации",
]

_DEFAULT_RANKS = [
    "Рядовой", "Ефрейтор", "Младший сержант", "Сержант", "Старший сержант",
    "Старшина", "Прапорщик", "Старший прапорщик", "Младший лейтенант", "Лейтенант",
    "Старший лейтенант", "Капитан", "Майор", "Подполковник", "Полковник",
    "Генерал-майор", "Генерал-лейтенант", "Генерал-полковник",
]

_DEFAULT_APPLICATION_METHODS = {
    "Собеседование": "",
    "Электронная заявка": "",
    "После КМБ": "",
}

_DEFAULT_AUDIT_ACTIONS = {
    "Принять": "",
    "Уволить": "",
    "Понизить": "",
    "Повысить": "",
}

MIN_MANAGE_RANK = "Капитан"

SERGEANT_RANK = "Сержант"

@dataclass(frozen=True)
class Settings:
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", ""))

    application_panel_channel_id: int = field(
        default_factory=lambda: _int_env("APPLICATION_PANEL_CHANNEL_ID", 0)
    )
    application_review_channel_id: int = field(
        default_factory=lambda: _int_env("APPLICATION_REVIEW_CHANNEL_ID", 0)
    )
    resignation_panel_channel_id: int = field(
        default_factory=lambda: _int_env("RESIGNATION_PANEL_CHANNEL_ID", 0)
    )
    resignation_review_channel_id: int = field(
        default_factory=lambda: _int_env("RESIGNATION_REVIEW_CHANNEL_ID", 0)
    )


    audit_panel_channel_id: int = field(
        default_factory=lambda: _int_env("AUDIT_PANEL_CHANNEL_ID", 0)
    )
    audit_log_channel_id: int = field(
        default_factory=lambda: _int_env("AUDIT_LOG_CHANNEL_ID", 0)
    )



    ss_role_id: int = field(default_factory=lambda: _int_env("SS_ROLE_ID", 0))
    base_role_id: int = field(default_factory=lambda: _int_env("BASE_ROLE_ID", 0))
    cadet_role_id: int = field(default_factory=lambda: _int_env("CADET_ROLE_ID", 0))
    fired_role_id: int = field(default_factory=lambda: _int_env("FIRED_ROLE_ID", 0))
    
    divider_position_id: int = field(default_factory=lambda: _int_env("DIVIDER_POSITION_ID", 0))
    divider_department_id: int = field(default_factory=lambda: _int_env("DIVIDER_DEPARTMENT_ID", 0))
    divider_rank_id: int = field(default_factory=lambda: _int_env("DIVIDER_RANK_ID", 0))
    divider_access_id: int = field(default_factory=lambda: _int_env("DIVIDER_ACCESS_ID", 0))

    protected_role_ids: list[int] = field(
        default_factory=lambda: _int_list_env("PROTECTED_ROLE_IDS", [])
    )

    button_cooldown_seconds: float = field(
        default_factory=lambda: float(os.getenv("BUTTON_COOLDOWN_SECONDS", "3"))
    )

    battalion_assignment_channel_id: int = field(
        default_factory=lambda: _int_env("BATTALION_ASSIGNMENT_CHANNEL_ID", 0)
    )

    roles_to_cleanup_names: list[str] = field(
        default_factory=lambda: _list_env("ROLES_TO_CLEANUP_NAMES", _DEFAULT_ROLES_TO_CLEANUP)
    )
    ranks: list[str] = field(
        default_factory=lambda: os.getenv("RANKS", "").split(",")
    )
    
    position_role_ids: dict[str, int] = field(
        default_factory=lambda: _str_int_dict_env("POSITION_ROLE_IDS", {})
    )
    application_methods: dict[str, str] = field(
        default_factory=lambda: _dict_env("APPLICATION_METHODS", _DEFAULT_APPLICATION_METHODS)
    )
    audit_actions: dict[str, str] = field(
        default_factory=lambda: _dict_env("AUDIT_ACTIONS", _DEFAULT_AUDIT_ACTIONS)
    )

    roles_to_cleanup_ids: list[int] = field(
        default_factory=lambda: _int_list_env("ROLES_TO_CLEANUP_IDS", [])
    )
    ranks_map: dict[str, int] = field(
        default_factory=lambda: _str_int_dict_env("RANKS_MAP", {})
    )
    department_role_ids: dict[str, int] = field(
        default_factory=lambda: _str_int_dict_env("DEPARTMENT_ROLE_IDS", {})
    )

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not self.discord_token:
            warnings.append("DISCORD_BOT_TOKEN не задан")
        if self.application_panel_channel_id == 0:
            warnings.append("APPLICATION_PANEL_CHANNEL_ID не задан")
        if self.application_review_channel_id == 0:
            warnings.append("APPLICATION_REVIEW_CHANNEL_ID не задан")
        if self.resignation_panel_channel_id == 0:
            warnings.append("RESIGNATION_PANEL_CHANNEL_ID не задан")
        if self.resignation_review_channel_id == 0:
            warnings.append("RESIGNATION_REVIEW_CHANNEL_ID не задан")

        if self.audit_panel_channel_id == 0:
            warnings.append("AUDIT_PANEL_CHANNEL_ID не задан")
        if self.audit_log_channel_id == 0:
            warnings.append("AUDIT_LOG_CHANNEL_ID не задан")

        if self.base_role_id == 0:
            warnings.append("BASE_ROLE_ID не задан")
        if self.cadet_role_id == 0:
            warnings.append("CADET_ROLE_ID не задан")
        if self.ss_role_id == 0:
            warnings.append("SS_ROLE_ID не задан — is_ss() всегда False")
        return warnings

settings = Settings()
