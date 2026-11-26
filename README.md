# CDP 控件采集器

Windows 小悬浮窗：在已登录的专用 Chrome 里右键标注控件，管理本页说明和先后顺序，再把完整上下文发到 ZCode 对话。ZCode 简短确认后，后续信息和任务由你在那个对话中继续输入。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。**v0.2.3：每个控件一行，窄浮窗保留发送进度与回执。** 单行界面已观察并获用户认可，发布尚未进行；实际检查范围见[版本说明](docs/releases/v0.2.3.md)和[验证记录](docs/cdp-picker-validation.md)。

[上一版 v0.2.2 Windows 发布包](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.2.2/CDP-Control-Picker-v0.2.2.zip)。

## 怎样使用

1. 点“启动 Chrome”启动或复用固定专用浏览器，由本人登录目标网站。程序会尝试显示并前置对应窗口；系统拒绝前置或找不到匹配窗口时会明确提示。
2. 点“刷新”，按标题和域名选页，再点“连接并开启采集”；到网页右键需要的控件，填写说明并保存。
3. 展开“本页控件 · N”。每项只显示序号、名称、“编辑”和“⋯”；过长名称尾部省略，完整名称和详细说明只在编辑弹窗查看。
4. 在该项“⋯”菜单中上移、下移或删除。需要说明前后关系时勾选“按此顺序操作”；移动会自动勾选，取消后只保留排列。
5. 点击“发给 ZCode”。独立发送区持续显示准备、处理中、用时、不定进度与终态；完成后可点“查看发送回执”，再到ZCode对应项目打开原对话继续。

主窗默认约280dip宽，顶部按“启动 Chrome / 刷新 / 记录 / 日志”→选页→“连接并开启采集 / 关闭”排列；“关闭”只断开采集，保留Chrome与页签。单行列表可收起并在有限视口内滚动，行高和名称省略按实际字体测量。配置按需打开，详情只在编辑弹窗。

## 可以改什么

人只需说明名称和用途，前置条件、预期结果可选；界面不要求选择允许动作。既有allowedActions仅为兼容资料保留，不替代实际任务授权。名称是人的标注名称，页面地址、DOM/AX名称、selector等定位证据只读，修改说明不会改网页或冒充新采集证据。后续怎样使用控件，由你在ZCode给出任务后继续探索。

删除确认后只移入本地回收区。旧页面包和已发ZCode对话保留当时快照，不会被新修改或删除回写。新标注默认按采集时间追加；勾选的次序是**用户声明的操作意图，尚未执行验证**。前置条件和预期结果也是说明，不是实际成功证据。

“复制交接说明”只复制供你粘贴到已有对话的短任务，复制不等于发送。同份成功或结果未知的内容会禁用发送；修改说明、集合或顺序后按新内容判断。“已经发送过”立即显示原回执，本次未重复提交；“等待补登记”只补登记；“结果待确认”保留原记录，不重发同份资料。公开回复摘要最多500字，不展示内部思考，不替代ZCode完整对话。

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
| [send_feedback.py](send_feedback.py) | 独立持久发送状态、计时和公开回执 |
| [browser_launcher.py](browser_launcher.py) | 专用Chrome启动/复用与对应窗口显现 |
| [desktop_index.py](desktop_index.py) | 版本化私有索引兼容登记 |
| [交接技能](skills/cdp-picker-handoff/SKILL.md) | 配置、顺序语义与首轮范围 |

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

本版仍只交接上下文。UI缓存用于及时反馈，最终防重复仍由后台内容指纹、OS工作区锁和交接日志保证。隐藏动作表单后的最终隔离suite195项中194通过、1项跳过，JS语法、隐藏控件及最终资源/9模块归档检查通过。可见单行、编辑取消、更多菜单和重开后持久回执已观察；其后的动作表单隐藏未再做可见重开，不混为同一实测。最终EXE大小及哈希见对应BUILD-INFO.json和SHA256SUMS.txt，发布尚未进行。
