# CDP 控件采集器

连接一份已登录的专用 Google Chrome，在网页上右键选择控件，补充它的功能说明，保存成项目资料，再交给已有的 ZCode CLI 整理能力草稿。

人的操作集中在桌面面板和网页浮层中。点击“启动专用 Chrome”可直接准备浏览器；采集器不会替人登录、下载或安装 Chrome、安装 skill，或修改 ZCode 配置。

## 运行独立 EXE

交付文件为 `CDP-Control-Picker.exe`。采集部分通过 CDP 直接连接 Chrome，运行 EXE 不需要安装 Python 或 Node.js。

需要提前准备：

- 已安装的 Google Chrome。程序可直接启动专用 CDP 窗口，也可复用已经开放的 CDP 入口。
- 一个用于保存采集资料的本地工作区文件夹。
- 如果要发送生成任务，电脑上还需有可用的 ZCode CLI；仅采集和保存不需要 ZCode。

程序默认填写本机 `http://127.0.0.1:9222`。点击“启动专用 Chrome”时，先检查该入口；已可用就复用，不重启现有浏览器。需要新建实例时使用 EXE 同目录中的固定 `CDP-Chrome-Profile`，不复制日常 Chrome 的资料。若自动发现程序失败，可点“Chrome 路径”选择已有 chrome.exe。你首次在专用窗口登录网站；会话过期后仍回到该窗口重新登录。

## 一次采集怎样完成

1. 打开程序，点击“启动专用 Chrome”。在该窗口打开服务网站并登录，然后回面板刷新网页列表。已有 CDP 实例时也可以直接刷新。
2. **先在面板选择目标页签，再连接并开启采集模式。** 核对网页和网站账号，避免选到同名页面。
3. 回到该网页，右键要说明的控件。采集浮层会提供名称、用途、允许动作和预期结果等填写位置。
4. 写清业务含义，例如“订单 A-1042 这一行的详情按钮，用于只读查看该订单”。保存后，资料进入选定的本地工作区。
5. 使用高亮验证检查定位是否指向预期控件。高亮与唯一匹配只是定位证据，不代表业务流程已验证成功。
6. 需要整理能力时，在“ZCode 任务与日志”页选择已有 CLI 并发送任务。程序只让它读取本次 `task.md`、生成草稿，不自动执行生成的网页操作。

第一版面向**主 frame 中的普通 DOM 控件**。iframe 内控件、Shadow DOM、Canvas 内部对象等不属于当前已确认的支持范围。网页导航、重绘或切换标签后，需要重新核对目标；旧节点不能当作永久位置。

## 保存了什么

工作区按统一采集编号组织：

```text
<工作区>/captures/cap-.../
  capture.json          页面、控件、定位候选及采集状态
  description.md        人填写的功能描述
  task.md               给 ZCode 的本次任务说明与技能引用
  generated/            ZCode 生成的能力草稿
  runs/run-.../
    run.json            运行状态、任务路径、会话与退出结果
    stdout.txt          CLI 标准输出
    stderr.txt          CLI 标准错误
```

采集保留页面范围、有限控件信息、候选 selector、业务说明和定位事实，排除 Cookie、token、密码输入值、私钥字段及明显输入值。页面 URL 不保存查询串或片段；不会复制真实浏览器 profile。

用户主动选择的控件文字和填写的描述仍可能包含业务信息，应将采集范围限定为本次需要的内容。脱敏处理不能代替对资料本身的判断。

## ZCode 会做什么

程序使用参数数组启动已选 CLI，不把描述拼接进 shell。任务保持一句：

> 读取“本次 task.md 的路径”，按其中说明整理能力草稿。

详细要求位于项目文件中。ZCode 按需读取本次采集 JSON、功能说明和仓库技能，输出到 `generated/`：

- `SKILL.md`：能力适用范围、参数、页面定位、业务含义及检查方法。
- `page-contract.json`：页面范围、候选定位、业务键和成功证据。
- `runner.py` 或 `runner.mjs`：候选前端执行脚本，未知配置保留明确占位符。

这些文件都是 **draft**。本次任务不连接或操作网页、不运行生成的 runner、不安装 skill，也不修改浏览器或 ZCode 设置。后续验证和业务执行需要单独指定任务与操作范围。

CLI 运行采用当前项目的单次执行模式。`completed` 表示收到了 CLI 结果，不能据此声称生成能力已经可用。非零退出记为失败；超时、停止或缺少可确认结果时保留未知状态，不自动重发。可以查看工作区、标准输出与原运行记录。

## 从源码启动

源码入口为 `app.py`，在带 Tk 的 Python 环境中运行 `python app.py`。建议使用独立虚拟环境；下面所有依赖安装都指向该虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

运行依赖只有 `websocket-client`，负责 CDP 的 WebSocket 传输；HTTP 页签发现、工作区和 CLI 调度使用 Python 标准库。PyInstaller 只在构建 EXE 的隔离环境中需要，不是源码运行依赖，也不需要最终使用者安装。

主要源文件：

| 文件 | 职责 |
|---|---|
| `app.py` | 桌面面板、操作状态与模块集成 |
| [cdp_client.py](cdp_client.py) | 连接现有 CDP 入口、发现与绑定页签、收发协议消息 |
| [page_picker.js](page_picker.js) | 采集模式、右键浮层和目标信息 |
| [workspace.py](workspace.py) | 脱敏、统一编号、原子保存与任务文件 |
| [zcode_runner.py](zcode_runner.py) | CLI 解析、异步执行、输出与状态记录 |
| [browser_launcher.py](browser_launcher.py) | 检测现有 CDP 或启动固定专用资料的 Chrome |
| [build.py](build.py) | 使用 PyInstaller 生成独立 Windows EXE |

## 当前验证范围

已完成 Python 离线测试 36 项（CDP 15、工作区与 fake CLI 8、启动器 13），以及页面采集 JavaScript 检查。fake CLI 检查了路径空格、短任务参数、JSON 结果、退出码和超时不重发；这些检查不调用真实模型。

另已完成真实隔离浏览器的右键采集、回传保存、刷新重定位、启动复用与持久资料测试，EXE 已完成启动检查。详细范围见[验证记录](../../docs/cdp-picker-validation.md)。这些检查不代表已经在用户真实网站、账号或服务器上完成业务实测。
