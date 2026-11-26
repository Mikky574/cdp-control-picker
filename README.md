# CDP 控件采集器

Windows 小悬浮窗：在已登录的专用 Chrome 里右键标注控件，管理本页说明和先后顺序，再把完整上下文发到 ZCode 对话。ZCode 简短确认后，后续信息和任务由你在那个对话中继续输入。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。**v0.2.1 新增本页控件管理。** 编辑、删除、排序和按需展开已有源码控件测试，数据与交接测试通过；[版本说明](docs/releases/v0.2.1.md)及[验证记录](docs/cdp-picker-validation.md)区分源码验证与成品资源检查。

[v0.2.1 Windows 发布包](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.2.1/CDP-Control-Picker-v0.2.1.zip)。

## 怎样使用

1. 启动或复用固定专用 Chrome，由本人登录目标网站。
2. 刷新并选页，连接后开启采集；右键需要的控件，填写说明并保存。同一页可标注多个。
3. 展开“本页控件”，查看“序号 / 名称 / 用途”。选中后可编辑、删除、上移或下移。
4. 需要说明前后关系时勾选“按此顺序操作”。上移或下移会自动勾选；取消勾选后，列表只作为排列。
5. 点击“发给 ZCode”。完整包携带最新说明及顺序，模型读取后简短确认并等待你。到ZCode对应项目打开对话，自行继续工作。

“本页控件”可随时收起，主界面继续是小悬浮窗。设置和编辑按需打开，日志留在文件。

## 可以改什么

编辑包含名称、功能说明、允许动作、前置条件、预期结果；名称和功能说明必填。名称是人的标注名称，页面地址、DOM/AX名称、selector等定位证据只读，修改说明不会改网页或冒充新采集证据。

删除确认后只移入本地回收区。旧页面包和已发ZCode对话保留当时快照，不会被新修改或删除回写。新卡默认按采集时间追加；勾选的次序是**用户声明的操作意图，尚未执行验证**。前置条件和预期结果也是说明，不是实际成功证据。

“复制交接说明”只复制供你粘贴到已有对话的短任务，复制不等于发送。相同内容已有对话时复用；编辑说明、删除或顺序声明变化会作为更新后的上下文处理。

## 交接的边界

首轮只读完整控件上下文、简短确认并等待用户，不自动生成技能、脚本或后续计划，不点击/填写控件、不执行业务命令。“按此顺序操作”描述你的意图，不会触发执行。你以后可以自行在ZCode提出新任务。

主路径为安装版 app-server stdio，保留本工具所选工作区/会话的 yolo“完全访问”，不改其他项目、全局模型/认证或Plaza角色。权限不扩大首轮任务。后台不依赖桌面焦点，启动、标注、编辑与排序不调用模型，点击“发给 ZCode”才提交交接。

当前适配ZCode **3.16.3.8992 / CLI 0.17.0**，目标电脑需已有Chrome与ZCode，便携包无需另装Python/Node。只支持普通主frame；iframe、Shadow DOM和Canvas内部对象不在范围。见[交接说明](docs/zcode-handoff.md)、[产品说明](docs/cdp-control-picker.md)及[当前限制](docs/known-issues.md)。

## 本地资料与源码

源码默认数据在.local/；冻结EXE的工作区和CDP-Chrome-Profile默认在EXE旁，设置仅本次运行生效。保留原目录可沿用仍有效的登录；过期时回同一专用Chrome重新登录。标注、回收资料、profile、完整会话、日志及索引备份都留本地，不入Git/Release。

| 文件 | 职责 |
|---|---|
| [app.py](app.py) | 小悬浮窗、本页列表与编辑入口 |
| [page_picker.js](page_picker.js)、[workspace.py](workspace.py) | 右键采集、标注管理、软删除和同页顺序 |
| [zcode_handoff.py](zcode_handoff.py)、[zcode_appserver.py](zcode_appserver.py) | 上下文交接与原生会话 |
| [desktop_index.py](desktop_index.py) | 版本化私有索引兼容登记 |
| [交接技能](skills/cdp-picker-handoff/SKILL.md) | 配置、顺序语义与首轮范围 |

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

0.2.1完整测试共144项：143项通过，1项真实符号链接测试因Windows权限跳过，替代的最终文件门禁测试通过。其中11项新增UI/管理测试（8项fake、3项隐藏Tk）与15项交接fake测试覆盖对应流程；数据测试覆盖持久化、并发、快照和页间隔离。本版没有新增真实模型、浏览器或最终EXE的GUI端到端测试，0.2.0的真实交接证据单独保留。最新EXE资源检查退出0，五项控件管理入口及JS/技能/图标齐备；归档7模块和0.2.1主模块字节码检查通过。包哈希以对应版本的SHA256SUMS.txt和BUILD-INFO.json为准。
