# 采集与交接配置

当前安装适配目标为 ZCode 3.16.3.8992 / 同包 CLI 0.17.0，不推断其他版本兼容。发布及最新首轮交接验收以随包记录为准。

## 入口

| 项目 | 约定 |
|---|---|
| CDP | http://127.0.0.1:9222 是本机浏览器端点，不是业务 URL |
| Chrome | 固定专用 Google Chrome/profile，由本人登录，复用已有端点 |
| 数据 | 源码默认 .local/；冻结 EXE 旁为 CDP-Picker-Workspace 与 CDP-Chrome-Profile |
| ZCode | 真实安装的 ZCode.exe 与同包 resources/glm/zcode.cjs |
| 主通道 | Electron Node 子进程运行 app-server --stdio，cwd 为采集工作区 |
| 权限 | 用户保留本工具选定工作区/会话 yolo 完全访问，不改其他项目或全局模型 |
| 记录 | .logs/picker.log 与 .handoffs/handoff-.../ 的 request/state/result |

设置目前仅本次运行生效。工作区应明确可写；不克隆日常 Chrome 默认资料，不重启共享浏览器，不导出 Cookie/token。端点可达不证明登录账号正确。

## 人的操作

刷新后明确选页，再连接并开启采集；多个同址页签不取第一个。同页分别右键保存控件名、用途、允许动作和预期结果，可展开“本页控件”，再点击“发给 ZCode”。“复制交接说明”提供读取本包的短任务，供人粘贴到已有会话；复制本身不提交模型。

合包按规范化 origin/path 或 file URL/path，忽略 query/hash，业务键仍需保留。只支持普通主 frame DOM；iframe、Shadow DOM、Canvas 内部对象不在范围。

## 本页标注与顺序

“本页控件”可展开/收起，列表显示序号、名称、用途。编辑弹窗可改名称、功能说明、允许动作、前置条件、预期结果；界面保存要求名称与功能说明非空，名称最多120字、其他文本字段最多1500字，页面地址与定位证据只读。编辑存于.capture-kit/page-state.json覆盖层，load/list/export合并最新annotation；原capture.json、description.md、page/control/createdAt和原始文件SHA不改，不把手写名称当作真实AX名称。删除需确认，仅移入本地回收区；旧交接包和已发会话仍是当时快照。

默认createdAt升序，新卡追加。上移/下移调整本页持久顺序并勾选“按此顺序操作”；也可取消勾选，只保留排列。勾选只声明人的步骤意图，不代表已验证或已经执行。发送携带sequence，按[交接字段说明](handoff.md)解释。

数据接口为list_page_captures(page)、page_sequence(page)、set_page_sequence(page,capture_ids,ordered=True)、update_capture(id,annotation)、delete_capture(id)，恢复接口restore_capture(id)。set_page_sequence须完整且恰好覆盖本页所有活动卡；update只接受annotation，description非空，preconditions等可为字符串或字符串列表。不要通过这些接口改定位证据、跨页排序或删除旧交接快照。

## 后台与首轮任务

zcode_handoff.py 调用 zcode_appserver.py，独立采用 Plaza runtime、SID 保留与订阅结构。ELECTRON_RUN_AS_NODE=1 只作用于子进程；沿用已有认证与模型，后台不依赖桌面焦点，不改全局设置或其他 Plaza 角色。

首轮只读 context.md、完整 page-bundle.json 的定位与用途，简短确认后等待用户继续。不自动生成能力文件、不规划后续、不连接网站或执行业务命令。yolo 是用户保留的执行模式，不是首轮扩展任务。范围写在技能与 context.md；短 prompt 只指向这项交接任务。旧 task.md、generation 字段和 generated/ 兼容保留，不读取旧 task.md，不把它们当本次任务。

绑定后记录真实 SID，订阅事件并单次发送。协议 ACK 不等于模型已读或完成，需终态与同 SID 最终回复。恢复旧会话若用原生 session/setMode，可能保存当前选定工作区的权限偏好，last_binding.permissionScope 区分该作用范围。

zcode_runner.py 的 CLI -p 留作源码 fallback，可选 mode、默认 edit，不是按钮主通道；fallback 测试不代替主通道验收。

## 索引与排错

模型请求前 preflight 只读核对 ProductVersion、四表 schema/约束与项目归属。索引数据基目录依次取 ZCODE_DATA_BASE_DIR、HOME、系统用户目录，相对位置 .zcode/v2/tasks-index.sqlite。

登记前只读核实原生主会话、目录、删除状态和 mode。写入前备份 .handoffs/index-backups/，事务插入新 task 及必要 local 项目/root；同 SID 正确映射时幂等回读。显式 update_owned_mode 只允许 ownership、同 SID/taskPath 匹配的本工具旧行只改 mode，其他列与外来任务不动，不迁移或删库。

- CDP：核对端点、选定页签、开关与主 frame。
- app-server：区分绑定 SID、受理 ACK、模型确认回复和实际终态。未知结果保留 SID，不重发。
- 索引：模型已确认但登记失败时只补登记，不再调用模型。相同标注已有对话则复用，不重新交接。
- GUI：到对应项目核对同 SID；不预设所有 build 热刷新，不用新会话冒充连续对话。

个人资料、凭据、完整会话与索引备份留本地，诊断仅取必要片段。独立技能包不含源码，不为阅读技能安装另一套运行环境。
