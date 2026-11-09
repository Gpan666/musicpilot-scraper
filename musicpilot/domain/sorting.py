"""音乐库列表的排序规则：排什么、往哪排、缺值摆哪。

界面上的每一个排序列头都是这里的 ``SORT_FIELDS``：进来的是 ``(维度, 方向)``，
出去的是一个排好序的列表。规则集中在一处，免得「按专辑排」在表格和分组视图里
各排各的。

缺值（时长读不出来、标签里没专辑 / 艺术家）永远排在**最后**，不随升降序翻到最前面：
一屏翻下去先看到「—」，比看不到缺值更糟。同一维度相等时按「专辑 → 轨号 → 文件名」
兜底，顺序稳定，两次刷新不会换位。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

ASC = "asc"
DESC = "desc"
DIRECTIONS = (ASC, DESC)
DIRECTION_LABELS = {ASC: "升序", DESC: "降序"}


@dataclass(frozen=True, slots=True)
class SortField:
    id: str
    label: str
    # 缺值摆最后要能判断「有没有值」，所以每个维度都配一个取值函数
    value: Callable[[Any], Any]


def _lower(text: Any) -> str:
    return str(text or "").strip().casefold()


def _item(track: Any, name: str) -> Any:
    """曲目可能是 ``LibraryTrack`` 也可能是 ``as_dict()`` 出来的字典，两种都认。"""
    if isinstance(track, dict):
        return track.get(name)
    return getattr(track, name, None)


def _meta(track: Any, name: str) -> Any:
    if isinstance(track, dict):
        return track.get(name)
    return getattr(_item(track, "metadata"), name, None)


def _number(value: Any) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


SORT_FIELDS: tuple[SortField, ...] = (
    # 默认：库里看到的顺序，按目录 + 文件名，和「什么都没排」时一模一样
    SortField("default", "默认（目录 + 文件名）", lambda t: _lower(_item(t, "path"))),
    SortField("added", "添加时间", lambda t: _number(_item(t, "created_at"))),
    SortField("modified", "修改时间", lambda t: _number(_item(t, "modified_at"))),
    SortField("duration", "时长", lambda t: _number(_item(t, "duration"))),
    SortField("album", "专辑", lambda t: _lower(_meta(t, "album")) or None),
    SortField("artist", "艺术家", lambda t: _lower(_meta(t, "artist")) or None),
)

_FIELDS = {field.id: field for field in SORT_FIELDS}
DEFAULT_SORT = "default"
DEFAULT_DIRECTION = ASC


def normalize(field: str | None, direction: str | None) -> tuple[str, str]:
    """把外部传来的排序参数收敛成「认识的维度 + 认识的方向」。

    不认识的维度退回默认排序，不认识的方向退回升序——宁可给出一个能看懂的列表，
    也不要因为前端多传了一个字段就让整页 500。
    """
    key = (field or "").strip().lower()
    way = (direction or "").strip().lower()
    return (
        key if key in _FIELDS else DEFAULT_SORT,
        way if way in DIRECTIONS else DEFAULT_DIRECTION,
    )


def sort_payload() -> list[dict[str, str]]:
    """给前端的选择项：维度名与默认方向一起回去，界面不用自己维护一份。"""
    return [{"id": field.id, "label": field.label} for field in SORT_FIELDS]


def sort_tracks(
    tracks: Iterable[T], field: str | None = None, direction: str | None = None
) -> list[T]:
    """按指定维度排序；缺值恒定垫底，同值按专辑 / 轨号 / 文件名兜底。"""
    key, way = normalize(field, direction)
    items = list(tracks)
    if key == DEFAULT_SORT:
        # 默认顺序原样返回：调用方拿到的仍是「目录 + 文件名」的枚举顺序
        if way == DESC:
            return items[::-1]
        return items

    getter = _FIELDS[key].value
    present = [item for item in items if getter(item) not in (None, "")]
    missing = [item for item in items if getter(item) in (None, "")]
    present.sort(key=lambda item: (getter(item), _tiebreak(item)), reverse=way == DESC)
    missing.sort(key=_tiebreak)
    return present + missing


def _tiebreak(track: Any) -> tuple[str, float, float, str]:
    """同一维度相等时的稳定次序：专辑 → 轨号 → 碟号 → 文件名。"""
    return (
        _lower(_meta(track, "album")),
        _number(_meta(track, "track_number")) or 0.0,
        _number(_meta(track, "disc_number")) or 0.0,
        _lower(_item(track, "name")) or _lower(_item(track, "path")),
    )
