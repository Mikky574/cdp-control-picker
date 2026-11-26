# 同页标注包、任务终态与续聊

主路径为 app-server stdio + 本任务 yolo + 桌面私有索引登记。当前适配安装版 3.16.3.8992；真实验证与发布状态以随包记录为准。

## 读取与产物

用户分别保存同页控件后，点击“整理本页标注（ZCode）”，导出 `page-bundles/page-.../{page-bundle.json,task.md,generated/}`。JSON 含完整重新脱敏卡、人的用途、captureId、来源相对路径及校验信息，不是编号列表或事件录制。

分组忽略 query/hash、不以 targetId 分组；相同 origin/path 不证明同一业务记录，保存排序不表示流程。单卡 `captures/cap-.../task.md` 仍可读取同目录 capture.json、description.md，产物写该卡 generated/。

1. 实际读取任务、页面包和引用技能；消息含路径不等于已加载正文。
2. 区分 DOM/AX 观察、locator 与匹配数、人的用途及结果；只按有证据的业务键连接控件关系。
3. 在当前 generated/ 输出统一 SKILL.md、page-contract.json 和候选 runner，缺流程或参数标待确认。
4. 可使用实际工具做格式、引用及代码语法检查，如实报告结果与未执行项。整理阶段不连接业务网站、不执行业务动作、不运行候选 runner、不自动安装技能。

用户授权本采集器任务 yolo 完全访问，允许按任务使用 Bash、Agent 等可用工具完成必要校验。权限与任务 scope 分开，保持上述产物范围。不改全局默认、模型认证或其他 Plaza 角色。

## app-server 主路径

`ZCodeHandoff(...).send(bundle, on_event=...)` 通过安装版 Electron Node 运行同包 app-server stdio，工作区为采集工作区，任务 mode=yolo。运行结构参考 Plaza：保留运行时、真实 sessionId 和事件订阅，不把一次 RPC 返回误作完整对话结果。

绑定返回 SID 后先持久记录，再发送 task。session/send 的 ACK 只说明受理；继续消费订阅事件，等待实际完成终态及同 SID 结果。超时、错误或归属不明时保留原请求与 SID，不自动再发送。启动、刷新、标注不调用模型，后台不依赖桌面焦点。

`zcode_runner.py` 保留 CLI `-p` 源码 fallback，构造参数数组，可选 mode，默认 edit。它不是当前整理按钮的运行路径，不拿 fallback 测试替代 app-server 验收。

## 索引与去重

真实任务完成后，使用当前 `desktop_index.py` 兼容登记。模型请求前只读 preflight；严格 ProductVersion 3.16.3.8992 与精确 schema，并只读核实真实 CLI 会话及目录/权限。新写入前备份 `.handoffs/index-backups/`，事务插入新 task 及必要 local 项目/root。同 SID 映射一致时幂等回读；显式 update_owned_mode 仅允许 owned-marker 与同 SID/taskPath 匹配的本工具旧行修改 mode，其他列与外来任务不动。不迁移或删库。

回执 `registered/indexRegistered` 仅表示登记；`guiVisibilityVerified` 仍为 false。`index_failed` 保留会话，相同内容重试仅补登记；`unknown` 不重发。页面与卡片内容指纹防止换 bundle 编号重复首轮。请求、state、同 SID result 保存在 `.handoffs/handoff-.../`，实际记录以当前回执为准。

## 原 SID 第二轮

首轮绑定真实 SID、工作区与页面包；用户到 ZCode 同项目打开原会话继续，悬浮窗没有续聊输入框。后台续聊应绑定相同 SID，不以标题、bundleId 或新会话替代；避免与桌面同时对同 SID 发起新轮。

task ACK、任务终态、索引登记、GUI 可见与共享首轮上下文的第二轮分别报告。索引写入不能代替可见对话或连续上下文验收；个人输出、完整会话、状态和索引备份不提交 Git。
