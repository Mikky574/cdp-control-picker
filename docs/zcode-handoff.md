# app-server、桌面索引与原会话续聊

v0.2.0 开发候选版，尚未发布。主路径是 app-server stdio + 用户授权的本任务 yolo。后台原 SID 第二轮、Bash 语法检查与 owned 索引 mode 更新已实测；GUI 已打开原会话完整历史；桌面第三轮已收到可见回复，最新候选 EXE 重新启动与最终整包仍待验收。

## “整理本页标注”传什么

用户在同一页分别右键标注并保存控件，点击“整理本页标注（ZCode）”导出：

~~~text
<采集工作区>/page-bundles/page-.../
  page-bundle.json
  task.md
  generated/
~~~

JSON 嵌入完整重新脱敏卡、人的用途、定位观察、captureId、来源相对路径和校验信息；不是仅传编号或记录所有网页事件。分组按规范化 origin/path 或 file URL/path，忽略 query/hash，不以 targetId 分组；业务键仍需保留，卡片排序不是操作流程。

模型收到一句读取指定 task.md 的任务，须实际读取本包和引用技能，在 generated/ 生成统一 skill、页面约定、候选 runner。单卡 captures/cap-.../{task.md,capture.json,description.md} 继续兼容。

## 实际后台运行

安装目标为 **ZCode 3.16.3.8992 / 内置 CLI 0.17.0**。`zcode_handoff.py` 调用独立的 `zcode_appserver.py`，结构参考 [Plaza runtime、SID 与订阅机制](plaza-zcode-reference.md)，不依赖 Plaza 安装目录。

~~~text
[<安装目录>/ZCode.exe,
 <安装目录>/resources/glm/zcode.cjs,
 app-server, --stdio, --cwd, <采集工作区>]
~~~

`ELECTRON_RUN_AS_NODE=1` 仅对这个子进程生效，参数数组直接传给进程。mode 通过原生会话协议设为 yolo，不把 `-p` 当主路径。沿用已有认证与模型，不改全局默认、凭据或其他 Plaza 角色，后台不依赖桌面焦点。

用户已授权本采集器任务 yolo 完全访问，可用实际工具完成格式、引用和语法检查。任务 scope 仍是生成 draft，不连接网站、不执行业务、不运行候选 runner、不自动安装技能。旧的临时工具禁令已撤销，未执行检查仍要如实报告。

## 保留 SID、订阅与完成证据

1. `bind()` 新建原生持久会话，或 `bind(原SID)` 恢复该会话。恢复时保留原工作区身份，找不到原会话就失败，不新建替代品。绑定后立即记录真实 SID。
2. `prompt(SID, content, input_id=...)` 先执行 session/subscribe 和基线 session/read，拒绝并发中的轮次。
3. session/send 单次提交并返回 ACK。ACK 仅表示受理，继续消费属于同 SID/inputId/turn 的事件。
4. 等本轮成功终态，再 session/read 最终快照，确认本轮输入、后续回复与无活跃轮次；必要安全读取可有界重试。
5. 取得同 SID 的完整结果后写本地 result.json，再进入索引登记。任务发送超时或归属不明时保留原 SID，不重新发模型任务。

恢复时需要改变旧会话权限的原生 session/setMode 可能保存当前采集工作区的上次权限选择；底层 last_binding.permissionScope 区分作用范围，不更改其他项目或角色。额外反向交互若未支持则明确阻塞，不伪造用户回答或批准。

CLI `-p` 的 `ZCodeRunner` 仍在源码作为 fallback，默认 mode=edit，可显式选 edit/plan/build/guarded/yolo；resume 参数保持所选 mode。它的测试或退出码不能代替主 app-server 路径验收。

## 私有索引登记

`DesktopTaskIndex` 是用户选择的私有 schema 兼容适配，不是官方公开同步 API。模型请求前只读 preflight 核对 ProductVersion 3.16.3.8992、精确四表 schema/唯一约束和项目归属。

登记时再只读核实真实 CLI 主会话、目录、删除状态与持久 mode，不能仅凭格式像 SID 的字符串插入。默认索引数据基目录依次取 ZCODE_DATA_BASE_DIR、HOME、系统用户目录，相对位置 .zcode/v2/tasks-index.sqlite。

新写入前 SQLite backup 到采集工作区 .handoffs/index-backups/。事务插入新 task 及必要 local 项目/root；同 SID 正确映射时幂等回读。显式 `register(..., mode='yolo', update_owned_mode=True)` 只允许 owned-marker 与同 SID/taskPath 匹配的本工具旧行仅更新 mode，其他列和外来任务不动。不迁移、删库或强行改归属。

## 状态、去重与第二轮

入口为 `ZCodeHandoff(workspace, commandPath=安装路径).send(bundle, on_event=...)`。本地 .handoffs/handoff-.../ 保存 request.json、state.json 与 result.json，state 记录真实 SID、mode、transport 和索引回执。

| 状态 | 含义与处理 |
|---|---|
| running | 已绑定 SID，任务正在执行；受理不等于完成 |
| registered | 任务结果已取得并登记索引；不表示 GUI 自动打开或可见已验证 |
| index_failed | 会话与结果保留；相同内容再次提交只补登记，不重调模型 |
| unknown | 结果不完整或不明；保留记录，不自动重发 |

去重按页面与完整卡片内容指纹，不靠 bundle 编号。索引回执 indexRegistered 与 guiVisibilityVerified 分别表示登记和 GUI 证明，不能合并为一个“全部完成”。

用户到 ZCode 同项目打开原 SID 继续沟通，悬浮窗没有续聊输入框。后台续聊恢复同 SID，不用标题、bundleId 或新会话替代；避免与桌面并发发送新轮。

本轮 115 项纯单元测试通过；真实可见 Chrome 两卡合集已验证，测试输入值未落盘，业务点击为 0。

真实 app-server 先仅恢复原 SID 并将 edit 改为 yolo、planEnabled=false，该元数据步骤 model_usage 增量为 0，permissionScope 为 selected-workspace-mode-preference。owned 索引写入前有备份，前后仅 mode 改变。随后在同 SID 唯一发送第二轮，模型正确保留订单号输入框和查询按钮的上下文，Bash 实际执行 Node --check 并 completed，exitCode=0、error=null；客户端捕获真实 turn.started、工具生命周期与最终完成快照。

这证明后台同会话连续性及该语法校验权限已生效。随后独立 CUA 激活现有 ZCode，项目条目已显示，打开可见首轮两卡草稿与后台第二轮完整历史，模式按钮为“完全访问”；本次没有重启，不保证其他 build 同样热刷新。桌面同会话第三轮已收到可见回复，正确说明两控件配合并保留“来自标注、尚未运行时验证”的边界；输入区恢复空闲，截图确认“完全访问”。最新候选 EXE 已构建并通过资源检查，等待用户手动运行 Start-Updated-Test.cmd 重新启动及完成最后整包验收。当前原生 ZCode GUI 通过不能代替最新采集器 EXE 全链路通过。固定化摘要见[验证记录](cdp-picker-validation.md)，不提交真实 SID、原响应或完整日志；当前不作发布声明。
