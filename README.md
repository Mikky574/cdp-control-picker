# CDP 控件采集器

Windows 小悬浮窗：在已登录的专用 Chrome 里右键标注控件，把同页保存的定位与用途发到 ZCode 对话。ZCode 读完后简短确认，接下来的信息和任务由你在那个对话中继续输入。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。当前版本为 **v0.2.0**。最新“只交接上下文并确认”的首轮、120项单元测试及EXE资源/归档检查已通过，见[验证记录](docs/cdp-picker-validation.md)及[已知问题](docs/known-issues.md)。[v0.1.0](https://github.com/Mikky574/cdp-control-picker/releases/tag/v0.1.0) 为旧面板单卡包。

[下载 v0.2.0 Windows 便携包](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.2.0/CDP-Control-Picker-v0.2.0.zip)。

## 怎样使用

1. 启动或复用固定专用 Chrome，由本人登录目标网站。
2. 刷新并明确选页，连接后开启采集；右键需要的控件，填写名称、用途、允许动作和预期结果后分别保存。
3. 点击“发给 ZCode”，把当前页已经保存的卡片合为页面包并交接。ZCode 首轮读取完整资料，简短确认控件和用途。
4. 在 ZCode 对应项目打开这条对话，自行补充信息、继续提问或开始后续工作。相同标注已有对话时直接复用，不重新交接。

例如，标注“订单号输入框”和“查询按钮”后，首轮只确认收到这两个控件及用途。采集器不替你规划下一步，也不自动生成技能、页面约定或执行脚本；你以后可以自行在 ZCode 要求这些工作。

“复制交接说明”只复制一句读取本包的任务，供你粘贴到已有对话。复制不等于已发送，消息有文件路径也不等于模型已读到正文。

页面包只含已保存标注，不记录全网页事件。保存顺序不代表操作流程，同页不代表同一业务对象。首轮不连接业务网站、不操作控件、不执行业务命令。

## 后台与边界

主路径是安装版 `ZCode.exe` 的 Electron Node 模式运行同包 `zcode.cjs app-server --stdio`。用户保留本工具选定工作区/会话 **yolo 完全访问**；首轮任务仍只读取上下文并确认，不改其他项目、全局模型/认证或 Plaza 角色。

后台不依赖桌面焦点。启动、刷新、标注不调用模型，点击“发给 ZCode”才提交交接任务。真实 SID、事件订阅与最终快照保留，协议 ACK 仅表示受理；模型确认、索引登记、GUI 可见分别核实。悬浮窗没有续聊输入框，不接管 ZCode 里的后续工作。

当前安装适配目标为 **ZCode 3.16.3.8992 / CLI 0.17.0**，不是跨版本保证。目标电脑需已有 Chrome 与 ZCode，最终便携包无需另装 Python/Node。实现见[交接说明](docs/zcode-handoff.md)和[Plaza 参考](docs/plaza-zcode-reference.md)。CLI -p runner 仅保留为源码 fallback。

## 本地资料

源码默认数据在 `.local/`；冻结 EXE 默认工作区与专用 profile 在 EXE 旁，设置目前仅本次运行生效。

| 位置 | 内容 |
|---|---|
| `captures/cap-.../` | 单卡定位与用途，继续兼容 |
| `page-bundles/page-.../` | 完整同页 page-bundle.json 与 context.md 交接入口 |
| `.handoffs/handoff-.../` | 请求、真实 SID、状态、模型确认与登记回执 |
| `.handoffs/index-backups/` | 索引写入前备份 |
| `.logs/picker.log` | 采集与交接日志 |
| `CDP-Chrome-Profile/` | 本人登录过的专用浏览器资料 |

桌面栏目采用用户选择的**私有索引兼容登记，不是官方公开同步 API**。先核对版本/schema及真实会话，备份后窄范围写入；仅 ownership、同 SID/taskPath 匹配的本工具旧行可只改 mode，外来任务和其他列不动。旧 task.md、generation 字段和 generated/ 仅为格式兼容保留，当前交接不读取或执行旧生成任务。个人采集、profile、凭据、完整会话、日志和备份不入 Git/Release。

## 开发入口

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

| 文件 | 职责 |
|---|---|
| [app.py](app.py) | 小悬浮窗、设置与发送入口 |
| [page_picker.js](page_picker.js)、[workspace.py](workspace.py) | 右键标注、脱敏保存与同页合集 |
| [zcode_handoff.py](zcode_handoff.py)、[zcode_appserver.py](zcode_appserver.py) | SID、订阅、首轮确认与结果 |
| [desktop_index.py](desktop_index.py) | 版本化私有索引适配 |
| [cdp-picker-handoff](skills/cdp-picker-handoff/SKILL.md) | 配置和上下文交接范围 |

最新首轮用新虚构页面两张卡完成：模型只读取 context.md 和完整 page-bundle.json，正确确认“查看申请按钮”和“申请编号输入框”的定位用途后等待用户，索引登记成功，未生成文件或发起后续任务。完整120项单元测试通过；最新EXE --package-check退出0，JS/技能/图标齐备，归档含app-server客户端、索引、交接、工作区等7个关键模块，无DesktopBridge。资源检查不打开窗口、不连浏览器、不调用模型，独立于GUI实测。采集器验收止于送达回执，后续工作由用户自行决定；包哈希见随包SHA256SUMS.txt和BUILD-INFO.json。
