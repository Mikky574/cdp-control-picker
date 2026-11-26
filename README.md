# CDP 控件采集器

独立的 Windows 网页控件采集项目。小置顶窗口连接本人已登录的固定专用 Chrome，在普通网页主页面右键控件、填写用途并保存；同页已标注控件可整理为一个页面任务包。

源码仓库：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。当前交付为开发源码，已知问题和下一步接入要求见 [known-issues.md](docs/known-issues.md)。

**直接使用程序：** [下载 v0.1.0 便携包](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.1.0/CDP-Control-Picker-v0.1.0.zip) · [单独下载 EXE](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.1.0/CDP-Control-Picker-v0.1.0.exe) · [Release 与对应源码](https://github.com/Mikky574/cdp-control-picker/releases/tag/v0.1.0)。解压后双击运行，采集功能无需 Python 或 Node。

发布包是此前已确认可操作的 v0.1.0 面板版，源码对应 `v0.1.0` 标签及 `release/0.1.x` 分支。下面的 `main` 源码说明包括仍在开发的悬浮窗与页面汇总，详见[版本说明](docs/releases/v0.1.0.md)。

这个目录包含完整采集器源码、图标、测试和两份配套技能，不依赖其他知识库的 HTML 或目录结构。原知识库副本继续保留。

## 使用与交接

1. 启动专用 Chrome，在该浏览器中登录目标网站。
2. 刷新并选定目标页，连接后开启采集。
3. 在网页中右键普通控件，填写功能说明并保存；可在同页继续标注多个控件。
4. 导出本页任务，交给 ZCode 整理统一页面 skill、页面约定和候选 runner。

页面任务包是人工标注合集，不记录页面全部事件，编号排序也不是操作顺序。整理任务默认只生成 draft 文件，不连接网站或执行真实业务；流程缺失时写明待验证项。

**尚未接入自动发送到 ZCode 可见对话。** 当前只生成本地任务文件并提供短任务，由用户手动粘贴到已有 ZCode 对话继续。后续“发送”的完成条件是任务在用户指定的对话中可见且可继续；后台 CLI 的独立会话、退出码或文件生成不能替代这项验收。本次独立目录整理未启动 GUI、浏览器或模型验证。

首次登录与登录失效后的重新登录均在同一个专用 Chrome 中完成。profile 可以保存仍有效的站点会话，不保证永久登录；工具不导出 Cookie 或 token。

## 源码运行

使用带 Tk 的 Windows Python 3.10 或更新版本，在本目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

源码模式的默认工作区是 `.local/CDP-Picker-Workspace/`，专用浏览器资料位于 `.local/CDP-Chrome-Profile/`。这些目录不进入 Git。冻结 EXE 仍在 EXE 所在目录旁使用 `CDP-Picker-Workspace/` 和 `CDP-Chrome-Profile/`，保留既有便携部署的路径习惯。

工作区中，单卡位于 `captures/cap-.../`；同页合集位于 `page-bundles/page-.../`，包含 `page-bundle.json`、`task.md` 和 `generated/`。日志保存在 `.logs/picker.log`。采集文件和浏览器资料均不随源码提交或打包。

## 测试与构建

纯单元测试不启动 Tk 窗口、真实 Chrome 或模型：

```powershell
python -m unittest discover -s tests -v
```

已构建 EXE 支持无窗口资源检查：

```powershell
CDP-Control-Picker.exe --package-check package-check.json
```

开发环境安装 PyInstaller 后构建；该步骤不会启动生成的程序：

```powershell
python -m pip install pyinstaller
python build.py --output dist --name CDP-Control-Picker
```

`build.py` 使用本目录的 `assets/`、`page_picker.js` 和 `skills/`，输出到 `dist/`，中间产物放在 `_build/`。需要重新生成图标时，另安装 Pillow 并运行 `python scripts/generate_icon.py`；图标源、PNG 与 ICO 均保存在 `assets/`。

## 文件入口

| 文件 | 作用 |
|---|---|
| [app.py](app.py) | 小悬浮窗、配置与采集状态、任务交接入口 |
| [cdp_client.py](cdp_client.py)、[browser_launcher.py](browser_launcher.py) | 附加指定 CDP 页签与启动/复用固定 Chrome |
| [page_picker.js](page_picker.js)、[workspace.py](workspace.py) | 右键标注、脱敏保存、同页合集 |
| [skills/cdp-page-contract](skills/cdp-page-contract/SKILL.md) | 固定浏览器、页面定位和业务含义约定 |
| [skills/cdp-picker-handoff](skills/cdp-picker-handoff/SKILL.md) | 配置、页面包与 ZCode 对话交接规则 |
| [docs/cdp-control-picker.md](docs/cdp-control-picker.md) | 控件采集方案与边界 |
| [docs/cdp-picker-validation.md](docs/cdp-picker-validation.md) | 移植前验证记录，区分历史实测与新仓验收 |

`zcode_runner.py` 是保留的可选 CLI 适配代码及测试对象，默认采集窗口不依赖它。新增 ZCode 对话接入时须保留明确的目标会话、可见发送结果与可继续性。

新仓的 Release 提供 v0.1.0 可用面板版。`main` 中的新界面与页面汇总尚未完成冻结 EXE 验收，不能将旧版发布与历史验证记录视为开发版已验收。
