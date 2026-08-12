from .mee import ADAPTER as mee
from .mnr import ADAPTER as mnr
from .mof import ADAPTER as mof
from .ndrc import ADAPTER as ndrc


ADAPTERS = {adapter.site: adapter for adapter in (ndrc, mee, mnr, mof)}


def get_adapter(name: str):
    try:
        return ADAPTERS[name.casefold()]
    except KeyError as exc:
        raise ValueError(f"不支持的来源：{name}") from exc


__all__ = ["ADAPTERS", "get_adapter"]
