# CDP 控件采集器

Windows 小悬浮窗：连接本人已登录的固定专用 Chrome，右键标注控件的位置和用途，把同页已保存卡片合为任务包，由 ZCode 后台整理统一能力草稿，再兼容登记到桌面对话索引。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。当前为 **v0.2.0 开发候选版，尚未发布**。后台原会话续聊、Bash 校验、owned 索引 mode 更新和 ZCode GUI 打开原历史均已实测；桌面同会话续聊已收到可见回复并恢复空闲；最新候选 EXE 的重新启动与最终整包仍待验收，见[验证记录](docs/cdp-picker-validation.md)、[已知问题](docs/known-issues.md)与[发布草稿](docs/releases/v0.2.0.md)。[v0.1.0](https://github.com/Mikky574/cdp-control-picker/releases/tag/v0.1.0) 是旧面板单卡包。

## 怎样使用

1. 启动或复用固定专用 Chrome，由本人在这个浏览器登录网站。
2. 刷新并明确选择页签，连接后开启采集；在普通主页面分别右键控件，填写用途、允许动作和预期结果后保存。
3. 点击“整理本页标注（ZCode）”，将本页已经保存的卡片合成页面包，后台整理为统一能力草稿。
4. 后台保留真实 sessionId，订阅任务进度，等实际完成后登记桌面索引。到 ZCode 同项目核对并打开这条会话，继续沟通。

“整理”会把控件位置、人的用途与预期结果汇成技能说明、页面约定和候选脚本。例如，标注订单号输入框与查询按钮后，可得到这两个控件的使用草稿；缺少的参数和操作关系标为待确认。页面包只含已保存标注，不记录网页全部事件，卡片顺序不代表流程。

用户已明确授权**本采集器任务使用 yolo 完全访问**。当前任务仍是生成 draft：允许格式、引用和语法检查，不连接网站、不执行业务动作、不运行生成的 runner、不自动安装技能。权限模式与产物范围分别说明，不再额外禁用工具。

启动、刷新、采集不调用模型；点击整理才提交任务。后台不依赖 ZCode 的桌面焦点。悬浮窗目前没有续聊输入框，用户在 ZCode 同项目打开原 session 继续。

## 后台与版本

主路径是同安装包 `ZCode.exe` 的 Electron Node 模式运行 `resources/glm/zcode.cjs app-server --stdio`，参考 Plaza 的运行时、SID 保留与事件订阅结构。`session/send` ACK 仅表示受理，完成还需本轮终态和同 SID 的最终快照。详见[交接说明](docs/zcode-handoff.md)及[Plaza 源码参考](docs/plaza-zcode-reference.md)。

当前核对的安装目标为 **ZCode 3.16.3.8992 / 内置 CLI 0.17.0**，不是跨版本支持声明。目标用户需已有 Chrome 和 ZCode；最终便携包自带采集器运行时，不要求另装 Python 或 Node。

`ELECTRON_RUN_AS_NODE=1` 仅作用于子进程；沿用已有认证与模型，不改全局默认、凭据或其他 Plaza 角色。`zcode_runner.py` 的 CLI `-p` 保留作源码 fallback，可选 mode、默认 edit，不是当前按钮通道。

## 本地资料

源码默认数据在 `.local/`；冻结 EXE 的工作区和固定 profile 默认在 EXE 旁，也可在设置中选工作区。设置目前仅本次运行生效。

| 位置 | 内容 |
|---|---|
| `captures/cap-.../` | 单卡、用途与单卡 task.md，继续兼容 |
| `page-bundles/page-.../` | 完整同页合集、统一 task.md、generated/ 草稿 |
| `.handoffs/handoff-.../` | 请求、state.json、真实 SID、result.json 与登记回执 |
| `.handoffs/index-backups/` | 索引写入前 SQLite 备份 |
| `.logs/picker.log` | 采集与交接日志 |
| `CDP-Chrome-Profile/` | 本人登录过的专用浏览器资料 |

桌面栏目采用用户选择的**私有索引兼容登记，不是官方公开同步 API**。适配器核对版本/schema、真实会话与目录/权限，备份后窄范围写入。只有 owned-marker、同 SID/taskPath 匹配的本工具旧行可仅更新 mode；外来任务和其他列不动。`registered/indexRegistered` 证明登记，不能代替 GUI 可见与第二轮验收。

个人采集、profile、凭据、会话、日志与索引备份不入 Git 或发布包。登录过期时回同一专用 Chrome 验证，不导出 Cookie/token。

## 开发与入口

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

构建机通过 `build.py` 与 PyInstaller 打包图标、JS、技能和后台模块。纯测试、真实模型、索引、GUI、续聊及最终包分别验收。

| 文件 | 职责 |
|---|---|
| [app.py](app.py) | 小悬浮窗、按需设置与整理入口 |
| [page_picker.js](page_picker.js)、[workspace.py](workspace.py) | 右键标注、脱敏保存与同页合集 |
| [zcode_handoff.py](zcode_handoff.py)、[zcode_appserver.py](zcode_appserver.py) | app-server 生命周期、真实 SID、事件与最终结果 |
| [desktop_index.py](desktop_index.py) | 版本化私有索引适配 |
| [zcode_runner.py](zcode_runner.py) | 入口解析与 CLI fallback |
| [cdp-picker-handoff](skills/cdp-picker-handoff/SKILL.md) | 配置、资料理解与交接技能 |

本轮 115 项纯单元测试通过。真实 Chrome 两卡采集已验证，测试输入值未落盘，业务点击为 0；app-server 在原 SID 完成唯一第二轮，正确保留两控件上下文，Bash 中的 Node 语法检查退出 0。元数据恢复/权限更新本身未增加模型用量，owned 索引有备份且只改 mode。当前安装版无需重启，激活现有 ZCode 窗口后已见项目任务条目；点开可见首轮两卡草稿与后台第二轮历史，模式为“完全访问”。桌面第三轮已收到回复，正确解释两控件配合并保留“源于标注、未经运行时验证”的边界，输入区恢复空闲；截图确认“完全访问”。最新 EXE 已构建并通过资源检查，等待用户手动重新启动验证。此观察不保证所有 build 热刷新，当前没有已发布的 v0.2.0 下载。
