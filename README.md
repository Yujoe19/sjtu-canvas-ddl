# SJTU Canvas DDL

## 兼容性

支持 Windows 命令行查询，已适配 Codex 定时提醒。手动查询只需要本机 Python、Edge 和你的交大 Canvas 登录会话，可以直接在 PowerShell 中使用。

能在同一 Windows 用户下执行本地命令的 AI 助手也可以调用查询脚本并读取 JSON 结果。目前已验证的聊天提醒集成是 Codex。接入其他助手时，需要由该工具负责定时运行和发送消息。

## 安装

需要 Windows、Python 3.11 或更高版本、Microsoft Edge，以及可以访问交大 Canvas 的学生账号。在 GitHub 仓库页面选择 **Code → Download ZIP**，解压后在含有 `SKILL.md` 的文件夹打开 PowerShell。安装到 Codex 时执行以下命令。

```powershell
$skillsRoot = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex\skills' }
$skillDir = Join-Path $skillsRoot 'sjtu-canvas-ddl'
New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
Copy-Item -LiteralPath 'SKILL.md', 'requirements.txt', 'scripts' -Destination $skillDir -Recurse -Force
Set-Location -LiteralPath $skillDir
python -m pip install -r requirements.txt
```

单独运行命令行工具时，在解压目录执行 `python -m pip install -r requirements.txt` 即可。

## 登录与查询

```powershell
python -X utf8 -B scripts/canvas_session.py
python -X utf8 -B scripts/check_deadlines.py
```

第一条命令会打开学校的 jAccount 登录页。在 Edge 中扫码或完成认证，看到「Canvas 登录已验证」后即可运行第二条命令。查询结果是 JSON，`items` 中包含课程、作业、北京时间和链接。只有状态为 `ok` 且 `items` 为空时，才表示本次完整查询没有找到待办。会话失效时重新执行登录命令。

## 在 Codex 中使用

打开 Codex 本地聊天，输入「用 $sjtu-canvas-ddl 查一下未来一周的作业」。需要定时提醒时，可以接着说「每天北京时间 09:00 和 21:00 检查，在这个聊天里提醒我」。检查在有登录会话的 Windows 电脑上运行。脚本本身只执行一次查询，定时运行由 Codex 自动化负责。

作业首次进入未来 168 小时、在范围内改期、或进入截止前 24 小时时会触发提醒。截止时间取当前学生在 Canvas 中实际看到的日期，包含个人或分组延期。已提交、已豁免的作业会被过滤。只写在公告、附件或作业正文里的日期不在查询范围内。

## 本地数据

首次登录使用独立的浏览器会话。脚本只保留交大 Canvas 域的会话 Cookie，并用 Windows DPAPI 在当前用户下加密。会话文件和用于提醒去重的记录保存在 `%LOCALAPPDATA%\sjtu-canvas-ddl\`。定时查询只读取 Canvas 课程数据。需要清除登录状态时，删除该目录中的 `canvas-session.dpapi` 即可。

## 测试与许可

在仓库根目录执行 `python -X utf8 -B -m unittest discover -s tests -v`。测试使用虚构作业和临时目录，离线即可运行。

[MIT 许可证](LICENSE)
