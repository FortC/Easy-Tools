"""快捷打开类动作（方案 2.3）。

安全约定：
- open.app 用 shutil.which 解析，解析不到再退回 os.startfile；
- open.command 明确 **不走 shell**，参数自行按 Windows 规则切分，
  避免把用户配置里的字符串直接拼进 cmd.exe 造成命令注入；
- 所有路径先做 expandvars/expanduser，%USERPROFILE% 这类变量可用。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import webbrowser
from typing import List

from ..log import get_logger
from .base import ActionContext, ActionResult, register

log = get_logger("openers")


def _expand_path(raw: str) -> str:
    return os.path.expandvars(os.path.expanduser((raw or "").strip().strip('"')))


def split_command_line(text: str) -> List[str]:
    """按 Windows 习惯切分命令行（支持双引号包裹）。"""
    lexer = shlex.shlex(text or "", posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return [t.strip('"') for t in lexer]
    except ValueError:
        return (text or "").split()


@register("open.app", "打开应用程序", "按程序名或路径启动应用", chain_id="open.run")
def _open_app(ctx: ActionContext, params) -> ActionResult:
    target = _expand_path(str(params.get("target", "")))
    if not target:
        return ActionResult.failure("未配置目标程序")
    exe = shutil.which(target)
    try:
        if exe:
            subprocess.Popen([exe] + split_command_line(str(params.get("args", ""))))
        else:
            os.startfile(target)          # type: ignore[attr-defined]
    except Exception as e:
        return ActionResult.failure("启动失败：%s" % e)
    ctx.message("已启动 %s" % target)
    return ActionResult.success("已启动 " + target)


@register("open.file", "打开文件/文件夹", "路径支持环境变量", chain_id="open.run")
def _open_file(ctx: ActionContext, params) -> ActionResult:
    target = _expand_path(str(params.get("target", "")))
    if not target:
        return ActionResult.failure("未配置路径")
    path = os.path.abspath(target)
    try:
        if os.path.isdir(path):
            os.startfile(path)           # type: ignore[attr-defined]
        elif os.path.exists(path):
            os.startfile(path)           # type: ignore[attr-defined]
        else:
            return ActionResult.failure("路径不存在：%s" % path)
    except Exception as e:
        return ActionResult.failure("打开失败：%s" % e)
    ctx.message("已打开 %s" % path)
    return ActionResult.success()


@register("open.url", "打开网址", "浏览器默认打开", chain_id="open.run")
def _open_url(ctx: ActionContext, params) -> ActionResult:
    url = str(params.get("url", "")).strip()
    if not url:
        return ActionResult.failure("未配置网址")
    if not url.startswith(("http://", "https://", "ftp://", "file://")):
        url = "https://" + url
    try:
        webbrowser.open(url)
    except Exception as e:
        return ActionResult.failure("打开失败：%s" % e)
    ctx.message("已打开 %s" % url)
    return ActionResult.success()


@register("open.command", "运行命令行", "不走 shell，参数显式切分", chain_id="open.run",
          params=[{"name": "command", "type": "text"}, {"name": "args", "type": "text"},
                  {"name": "cwd", "type": "text"}])
def _open_command(ctx: ActionContext, params) -> ActionResult:
    command = str(params.get("command", "")).strip()
    if not command:
        return ActionResult.failure("未配置命令")
    argv = [command] + split_command_line(str(params.get("args", "")))
    cwd = _expand_path(str(params.get("cwd", ""))) or None
    if cwd and not os.path.isdir(cwd):
        cwd = None
    try:
        subprocess.Popen(argv, cwd=cwd, shell=False)
    except Exception as e:
        return ActionResult.failure("执行失败：%s" % e)
    ctx.message("已执行 %s" % " ".join(argv))
    return ActionResult.success()


@register("open.explorer_select", "在资源管理器中定位", "选中文件所在目录", chain_id="open.run")
def _explorer_select(ctx: ActionContext, params) -> ActionResult:
    path = _expand_path(str(params.get("target", "")))
    if not os.path.exists(path):
        return ActionResult.failure("路径不存在：%s" % path)
    try:
        subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
    except Exception as e:
        return ActionResult.failure("定位失败：%s" % e)
    return ActionResult.success()
