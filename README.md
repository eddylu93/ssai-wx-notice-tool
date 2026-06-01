# SSAI-WX 通知小工具

这是一个 macOS 本地聊天式小工具，用于在类似微信聊天窗口的界面里输入通知文字、图片和视频，并发送到多个已经拆分出来的微信聊天窗口。界面使用 PySide6 实现，发送核心仍然只操作你已经拆分出的微信窗口。

![SSAI-WX 通知小工具界面预览](docs/app-screenshot.png)

- 版本：V1.0.9
- 联系方式：微信 sanshengya88

它不会调用微信接口，也不会自动搜索群聊。你需要先在微信里把目标群聊拆成独立窗口，工具会按检测到的窗口列表连续发送。

## 安装

```bash
git clone git@github.com:eddylu93/ssai-wx-notice-tool.git
cd ssai-wx-notice-tool
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

当前应用启动器会优先使用项目内的虚拟环境：

```bash
.venv/bin/python
```

如果系统提示没有 `pip`，先运行：

```bash
python3 -m ensurepip --upgrade
```

备用命令行运行方式：

```bash
.venv/bin/python app.py
```

## 打包

macOS 打包可在项目根目录运行：

```bash
chmod +x packaging/macos/build_macos.sh
packaging/macos/build_macos.sh
```

打包结果会输出到：

```text
release/macos/SSAI-WX-Notice-Tool-V当前版本-macOS-arm64.dmg
release/macos/SSAI-WX-Notice-Tool-V当前版本-macOS-arm64.pkg
```

打包脚本会从 `app.py` 的 `APP_VERSION` 读取版本号，并同步写入 macOS App 的 `CFBundleShortVersionString` 和 `CFBundleVersion`。

Windows 打包可在 Windows 项目根目录运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
packaging\windows\build_windows.ps1
```

打包结果会输出到 `dist\SSAI-WX-Notice-Tool\`。更多说明见 `packaging/windows/README.md`。

## macOS 权限

需要给运行工具的终端或 Python 授权“辅助功能”：

安装版首次打开时会自动提示开启辅助功能权限，并提供“去开启权限”按钮跳转到系统设置。没有授权时，工具无法读取拆分微信窗口，也无法点击微信输入框或执行粘贴发送。

1. 打开“系统设置”。
2. 进入“隐私与安全性”。
3. 打开“辅助功能”。
4. 勾选 `SSAI-WX 通知小工具`。如果是源码运行，则勾选你运行 `python3 app.py` 的终端应用，例如 Terminal、iTerm 或 Codex。

如果没有授权，工具可能无法激活微信窗口、粘贴或按回车发送。

## 使用步骤

1. 打开并登录微信 Mac 客户端。
2. 把本次要通知的群聊拆分成独立聊天窗口。
3. 双击打开：

```text
WX通知小工具.app
```

如果 macOS 阻止打开，也可以双击：

```text
打开通知小工具.command
```

备用命令行运行方式：

```bash
.venv/bin/python app.py
```

4. 在底部输入框输入通知文字。
5. 点击输入框左侧 `+` 添加图片、视频或文档，图片/视频也可以直接拖拽或复制粘贴到输入框。
6. 点击“刷新窗口”，确认右侧列表里只有本次要发送的拆分群聊窗口。
7. 点击“同步发送”。
8. 工具会先把你的内容显示成聊天气泡，再同步发送到检测到的拆分窗口。

图片和视频会在输入区下方显示附件预览，可点击预览旁的 `×` 删除。工具会先发送文字，再发送一批附件。每个窗口之间会自动随机等待 1-4 秒。

文档支持 `.txt` 和 `.docx`，`.doc` 老格式请先另存为 `.docx`。文档会按段落优先拆分，单条超过 300 字时继续按标点和字数切成多条消息。`.docx` 里的图片和视频会被抽取出来，每个媒体文件会作为单独任务发送；视频任务会额外等待更久，降低未上传完成就继续下一条的风险。上传文档后会显示文字条数和媒体数量，点击“同步发送”后每一条都会作为独立任务依次进入队列。文档模式不和手动附件混发；上传文档会清空当前附件预览。

发送过程中可以继续输入下一条通知并按 Enter 或点击按钮，新通知会进入队列；当前通知处理完后，工具会自动发送队列里的下一条。

发送中可点击“暂停”，工具会在当前窗口动作结束后暂停队列，不会清空等待任务。暂停后按钮变为“继续”，点击后从暂停位置继续处理。

发送中可点击“停止”清空等待队列。已经开始粘贴或发送的当前动作无法撤回，工具会在当前窗口动作结束后停止继续处理后续窗口和后续任务。

小工具默认置顶。如果不想让它一直浮在微信旁边，可以取消“置顶”。

## 试运行

勾选“试运行，不真正发送”后，工具会按检测到的窗口列表走完整个流程并记录结果，但不会粘贴或发送内容。建议第一次使用时先试运行。

## 发送记录

发送记录会写入：

```text
~/Library/Application Support/SSAI-WX 通知小工具/send_log.jsonl
```

每行是一条 JSON 记录，包含时间、窗口标题、文字摘要、附件文件名、状态和错误信息。

## 常见问题

### 没有检测到微信窗口

确认微信已经打开，并且群聊已经拆分成独立窗口。然后点击“刷新窗口”。

### 图片或视频没有发送成功

确认已经安装依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

附件发送依赖 macOS 剪贴板能力。如果图片格式异常，建议换成 PNG 或 JPG；如果视频过大，需要等待微信上传完成后再继续发送下一条。

### 工具粘贴了内容但没有发送

确认当前微信输入框可用，并检查 macOS 辅助功能权限。发送失败会写入同步记录和 `send_log.jsonl`。

V1.0.2 起，工具会在发送前主动点击拆分微信窗口底部输入区，再执行粘贴和回车发送。如果仍然没有发出，请先确认“系统设置 > 隐私与安全性 > 辅助功能”里已经允许 `SSAI-WX 通知小工具`。

### 会不会误发

工具不会自动搜索群聊，也不会后台静默发送。开始前会显示检测到的窗口列表；如果列表里有多余窗口，请先关闭或最小化后刷新。
