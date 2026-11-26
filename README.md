# CDP 控件采集器

Windows 小悬浮窗：在已登录的专用 Chrome 里右键标注控件，管理本页说明和先后顺序，再把完整上下文发到 ZCode 对话。ZCode 会连接这份已登录浏览器，检查定位并尝试验证已说明的操作；结果与可复用能力保存在同一工作区，对话可继续补充。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker/tree/codex/cdp-learning-v0.3.0)。**v0.3.0候选：每个控件一行，保留窄浮窗与持续发送反馈。** 本候选尚未发布；单行改版后的测试、构建与可见窗口检查待回填。[版本说明](docs/releases/v0.3.0.md)和[验证记录](docs/cdp-picker-validation.md)区分新候选与此前证据。

[上一版 v0.2.2 Windows 发布包](https://github.com/Mikky574/cdp-control-picker/releases/download/v0.2.2/CDP-Control-Picker-v0.2.2.zip)。

## 怎样使用

1. 点“启动 Chrome”启动或复用固定专用浏览器，由本人登录目标网站。已有浏览器会尝试显现并前置；若系统拒绝前置或没有可匹配窗口，界面会明确提示，请自行切到专用Chrome。
2. 点“刷新”，按标题和域名选页，再点“连接并开启采集”；到网页右键需要的控件，填写说明并保存。
3. 展开“本页控件 · N”。每项只显示序号、名称、“编辑”和“⋯”；名称过长时尾部省略，完整名称和详细说明在编辑弹窗中查看。
4. 在该项“⋯”菜单中选择上移、下移或删除。需要说明前后关系时勾选“按此顺序操作”；上移或下移会自动勾选，取消后列表只作为排列。
5. 点击“发给 ZCode”。独立发送区会保留准备、处理中、完成或待处理结果；处理中显示用时和不定进度。完成后点“查看发送回执”可看控件数量、对话名及公开回复摘要，再到ZCode对应项目打开对话继续。

主窗默认约280dip宽，顶部依次为“启动 Chrome / 刷新 / 记录 / 日志”、页签选择、“连接并开启采集 / 关闭”。“关闭”仅断开采集，保留Chrome和页签。“本页控件”可收起，单行项在有限视口内滚动；行高和名称省略按实际字体尺寸计算，详情只在编辑弹窗，配置在“设置”。

## 可以改什么

编辑包含名称、功能说明、允许动作、前置条件、预期结果；名称和功能说明必填。名称是人的标注名称，页面地址、DOM/AX名称、selector等定位证据只读，修改说明不会改网页或冒充新采集证据。

删除确认后只移入本地回收区。旧页面包和已发ZCode对话保留当时快照，不会被新修改或删除回写。新卡默认按采集时间追加；勾选的次序是**用户声明的操作意图，尚未执行验证**。前置条件和预期结果也是说明，不是实际成功证据。

“复制交接说明”只复制供你粘贴到已有对话的短任务，复制不等于发送。同一份内容成功发送或结果未知后，发送按钮会禁用；编辑说明、删除或顺序声明变化后可交接新内容。“已经发送过”立即显示，没有再次提交模型；“等待补登记”再次点击仅补桌面对话登记。结果未知时先核对原对话，不重发同份资料。

## 学习与验证

新包使用 learn-and-verify 模式：先真实连接检查、核对 selector，再按人的用途和明确顺序尝试性质清楚的读取或可撤销操作。填写可能自动保存，点击可能提交业务；业务提交、删除、付款或作用不明的步骤需要具体任务授权。缺输入参数就列出缺项，不编造生产数据。

每个页面包的 learning/ 保存工具回执、learning-report.json、SKILL.md、page-contract.json 和带参数的 runner.json/调用步骤。连接成功、定位成功和流程跑通分别记录；没有实际工具证据的不写已验证。旧包无学习意图时仍按原接收语义处理。

EXE 内含无界面 CDP 入口，ZCode 不用临时安装 Python/Node/Playwright。当前适配 ZCode 3.16.3.8992 / CLI 0.17.0；桌面对话仍由原生 sessionId 登记。handoff 编号只用于内部发送日志，不参与网页定位。见[编号与学习流程图解说明](docs/cdp-learning.md)和[学习技能](skills/cdp-learn-and-verify/SKILL.md)。

## 本地资料与源码

源码默认数据在.local/；冻结EXE的工作区和CDP-Chrome-Profile默认在EXE旁，设置仅本次运行生效。保留原目录可沿用仍有效的登录；过期时回同一专用Chrome重新登录。标注、回收资料、profile、完整会话、日志及索引备份都留本地，不入Git/Release。

| 文件 | 职责 |
|---|---|
| [app.py](app.py) | 小悬浮窗、本页列表与编辑入口 |
| [page_picker.js](page_picker.js)、[workspace.py](workspace.py) | 右键采集、标注管理、软删除和同页顺序 |
| [zcode_handoff.py](zcode_handoff.py)、[zcode_appserver.py](zcode_appserver.py) | 上下文交接与原生会话 |
| [send_feedback.py](send_feedback.py) | 持久发送状态、用时、去重提示和公开回执 |
| [browser_launcher.py](browser_launcher.py) | 专用Chrome启动/复用与准确窗口显现 |
| [desktop_index.py](desktop_index.py) | 版本化私有索引兼容登记 |
| [交接技能](skills/cdp-picker-handoff/SKILL.md) | 配置、顺序语义与首轮范围 |

~~~powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

当前学习验证版在独立分支 codex/cdp-learning-v0.3.0 开发，保留已有窄窗、发送回执及Chrome窗口恢复改动。学习模式的自动化测试与本地演示验收在验证记录中单独列出，不把历史接收回执当作新能力已跑通。包哈希以SHA256SUMS.txt和BUILD-INFO.json为准。
