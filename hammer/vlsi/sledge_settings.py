"""Read SledgeHammer's own config keys one way everywhere: typed, and tolerant of the usual spellings."""
from typing import Any, Optional

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")


def _raw(driver: Any, key: str) -> Any:
    try:
        return driver.database.get_setting(key, nullvalue=None, check_type=False)
    except Exception:
        return None


def flag(driver: Any, key: str, default: bool) -> bool:
    """A boolean setting: true/false, 1/0, or yes/no/on/off in any case. Unset or null gives the default."""
    val = _raw(driver, key)
    if val is None:
        return default
    if isinstance(val, (bool, int)):
        return bool(val)
    if isinstance(val, str) and val.strip().lower() in _TRUE + _FALSE:
        return val.strip().lower() in _TRUE
    log = getattr(driver, "log", None)
    if log is not None:
        log.warning(f"{key}: {val!r} is not true or false; using {default}")
    return default


def text(driver: Any, key: str) -> Optional[str]:
    """A string setting, or None when it is unset, null or empty."""
    val = _raw(driver, key)
    return None if val is None or str(val).strip() == "" else str(val)
