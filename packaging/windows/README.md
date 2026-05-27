# Windows 打包说明

当前版本仍是 macOS 专用版本，Windows 安装包暂未生成。

原因：

- `app.py` 启动时限制 `sys.platform == "darwin"`。
- 微信窗口检测使用 macOS AppleScript。
- 激活窗口使用 macOS 辅助功能。
- 剪贴板文件复制使用 macOS AppKit / Pasteboard。
- 粘贴和回车发送使用 macOS `System Events`。

Windows 版需要先实现 Windows 后端：

- 窗口检测：`pywinauto` / Win32 API。
- 激活窗口：Win32 窗口句柄。
- 文本剪贴板：`pyperclip` 或 `win32clipboard`。
- 文件剪贴板：Windows `CF_HDROP`。
- 粘贴发送：`pyautogui.hotkey("ctrl", "v")` + `press("enter")` 或 `pywinauto`。

已预留脚本：

```powershell
packaging\windows\build_windows.ps1
```

等 Windows 后端实现后，需要在 Windows 机器上运行该脚本生成 `.exe`，再接 Inno Setup 或 NSIS 生成安装包。

