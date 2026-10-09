# Copyright (c) ModelScope Contributors. All rights reserved.
"""One-line notices for memory writes the user can act on.

TUI's default log level is ERROR, so a warning in the log is invisible.
These sentences are what the session shows instead.
"""

CHAR_LIMIT_NOTICE = '记忆写不进去：已超过字数上限。请删短 MEMORY.md 后再试。'
DISK_NOTICE = '记忆写不进去：磁盘或权限不允许写入。请检查记忆目录的权限和剩余空间。'
QUIT_WHILE_WRITING = '记忆还在写入，这次退出可能没保存完。请重新打开会话确认 MEMORY.md。'
