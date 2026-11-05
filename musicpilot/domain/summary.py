"""任务明细「说明」一栏的中文摘要。

明细里存的是完整过程（写标签、转移结果、匹配理由），一条能上百字，还夹着
``inode``、``OSError``、``musicbrainz`` 这类英文。这里只做一件事：把完整文本
重新组织成两句中文短句，供表格与 CSV 使用；原文仍留在数据里，悬停可见。
"""

from __future__ import annotations

import re
from typing import Any

from musicpilot.domain.transfer import mode_label

# 已知的英文片段 → 中文说法，按长度倒序替换，避免「已复制」被「复制」二次命中
_PHRASES: tuple[tuple[str, str], ...] = (
    ("与目标共享同一 inode，不额外占用空间", "与源文件共用同一份数据"),
    ("与源文件共用同一份数据", "与源文件共用同一份数据"),
    ("源与目标已是同一个文件", "源文件与目标已经是同一个文件"),
    ("无法创建目标目录，请检查权限", "建不了目标目录，请检查权限"),
    ("目标路径越出音乐库目录，已拒绝", "目标路径跑到音乐库外面了，已拒绝"),
    ("硬链接要求源与库在同一挂载点", "硬链接要求源目录与音乐库在同一挂载点"),
    ("目标已存在，已自动改名", "目标已存在，已自动改名"),
    ("目标已存在，已覆盖", "目标已存在，已覆盖"),
    ("目标已存在，已跳过", "目标已存在，已跳过"),
    ("已复制进库，源文件保留", "已复制进库，源文件保留"),
    ("跨磁盘：已复制进库并删除源文件", "跨磁盘：已复制进库并删掉源文件"),
    ("已移动进库，源文件已删除", "已移动进库，源文件已删掉"),
    ("已建硬链接", "已建硬链接"),
    ("已建软链接，指向源文件", "已建软链接，指向源文件"),
    ("标签未写入（格式不支持或无可用字段）", "标签没写成（格式不支持或没有可写字段）"),
    ("无法生成目标路径，请检查命名模板", "算不出目标路径，请检查命名模板"),
    ("处理出错，详见日志", "处理出错，详见日志"),
    ("匹配度不足，已跳过", "匹配度不够，已跳过"),
    ("未查在线，只用本地标签", "没查在线，只用本地标签"),
    ("在线无结果，保留原标签", "在线没查到，保留原标签"),
    ("采用预览页选择", "按你在预览页里选的写入"),
    ("仅写标签，不整理文件", "只写标签，不整理文件"),
    ("musicbrainz", "MusicBrainz"),
    ("netease", "网易云"),
    ("local", "本地标签"),
    ("OSError", "文件操作失败"),
    ("PermissionError", "权限不足"),
    ("FileNotFoundError", "文件不存在"),
    ("FileExistsError", "目标已存在"),
    ("inode", "数据块"),
)

# 异常原文（``: [Errno 13] Permission denied: /path``）只作补充，不进摘要
_EXCEPTION_TAIL = re.compile(r"：[^。；]*\[[^\]]*\][^。；]*")
_ENGLISH_TAIL = re.compile(r"[A-Za-z][A-Za-z0-9_.:/\[\] ]{12,}")

_STATUS_LABELS = {
    "done": "成功",
    "skipped": "跳过",
    "failed": "失败",
    "planned": "计划中",
    "needs_review": "需确认",
}


def _strip_exception(text: str) -> str:
    return _EXCEPTION_TAIL.sub("（详情见日志）", str(text or ""))


def _clean(text: str) -> str:
    value = str(text or "")
    for source, target in _PHRASES:
        value = value.replace(source, target)
    return value.replace(" 。", "。").replace("，，", "，").strip()


def brief_detail(detail: str, *, mode: str = "", status: str = "") -> str:
    """Two short Chinese sentences: what happened, and why."""
    text = _clean(_ENGLISH_TAIL.sub("", _strip_exception(detail)))
    if not text:
        label = _STATUS_LABELS.get(status, "")
        way = mode_label(mode) if mode else ""
        if label and way:
            return f"{way}：{label}。"
        return f"{label}。" if label else "—"

    parts = [part.strip() for part in text.split("。") if part.strip()]
    head: list[str] = []
    for part in parts:
        head.append(part)
        if len(head) >= 2:
            break
    summary = "。".join(head) + "。"
    if len(summary) > 60:
        summary = f"{summary[:59]}…"
    return summary


def detail_payload(item: dict[str, Any]) -> dict[str, Any]:
    """附加 ``detail_brief``：表格里显示的短句，完整原文仍在 ``detail``。"""
    return {
        **item,
        "detail_brief": brief_detail(
            str(item.get("detail") or ""),
            mode=str(item.get("mode") or ""),
            status=str(item.get("status") or ""),
        ),
    }
