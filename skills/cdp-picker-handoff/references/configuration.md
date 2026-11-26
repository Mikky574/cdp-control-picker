# 采集与 app-server 配置

当前适配目标为安装版 ZCode 3.16.3.8992 / 内置 CLI 0.17.0，不推断其他版本兼容。发布与真实索引、GUI、第二轮的验收状态以随包记录为准；v0.1.0 为旧面板单卡流程。

## 核对入口

| 项目 | 约定 |
|---|---|
| CDP | `http://127.0.0.1:9222` 是本机浏览器端点，不是业务 URL |
| Chrome | 固定专用 Google Chrome/profile，由本人登录；复用已有端点 |
| 默认数据 | 源码在 `.local/`；冻结 EXE 旁为 `CDP-Picker-Workspace` 与 `CDP-Chrome-Profile` |
| ZCode | 真实安装的 `ZCode.exe` 与同包 `resources/glm/zcode.cjs` |
| 运行方式 | Electron Node 子进程启动 app-server，stdio 协议；工作区为采集工作区 |
| 本任务权限 | 用户明确授权 `mode=yolo` 完全访问，仅作用于采集器任务 |
| 记录 | `.logs/picker.log` 与 `.handoffs/handoff-.../` 的 request/state/result |

设置目前仅本次运行生效。工作区应明确且可写；不克隆日常 Chrome 默认资料，不重启共享浏览器，不导出 Cookie/token。端点可达不证明登录账号正确。

刷新后明确选择页签，再连接并开启采集；多页不取第一个。同页分别右键标注并保存用途、允许动作与预期结果，点击“整理本页标注（ZCode）”生成合集。分组按规范化 origin/path 或 file URL/path，忽略 query/hash；业务键仍需保留。iframe、Shadow DOM、Canvas 内部对象不在当前采集范围。

## 后台运行与权限

主路径由 `zcode_handoff.py` 调用 `zcode_appserver.py`，参考 Plaza 的 runtime、会话 SID 保留与事件订阅结构，独立封装在本仓库。安装版 ZCode.exe 以子进程 `ELECTRON_RUN_AS_NODE=1` 运行同包 app-server，后台不依赖桌面焦点。沿用已有认证与模型，不改全局默认、全局环境或其他 Plaza 角色，不打印密钥。

用户已明确授权本任务使用 yolo 完全访问，可按任务使用可用工具。任务范围仍是生成能力 draft，允许必要的格式/语法检查；不据权限扩大为连接网站、执行业务、运行候选 runner 或自动安装技能。未执行的校验如实说明。

启动、刷新、标注不发模型任务。整理时先获得并保存真实 SID，再发送该任务并订阅事件；session/send ACK 是受理，必须等实际终态和同 SID 结果。`zcode_runner.py` 仍支持 CLI `-p` fallback，默认 mode=edit，可显式选 edit/plan/build/guarded/yolo；它不是主按钮通道。

## 索引登记

用户选择的私有索引适配不是官方公开 API。模型请求前 `preflight()` 只读核对 ProductVersion、四表 schema、唯一约束和项目归属。索引数据基目录依次取 `ZCODE_DATA_BASE_DIR`、`HOME`、系统用户目录，相对位置 `.zcode/v2/tasks-index.sqlite`。

登记前只读核实真实 CLI 会话、目录、删除状态与权限。新写入前 SQLite backup 到采集工作区 `.handoffs/index-backups/`；事务插入新 task 及必要 local 项目/root，同 SID 正确映射时幂等回读。显式 `update_owned_mode=True` 只允许本工具 owned-marker 与同 SID/taskPath 匹配的旧行修改 mode，其他列与外来任务不动。未知 schema 停止，不迁移或删库；`indexRegistered` 不代表 GUI 可见。

## 按阶段排错

- CDP：区分端点、无普通页、采集开关、错误页签与不支持的 frame。
- app-server：区分启动、绑定 SID、task 受理、事件进度、实际终态；ACK 或进程存活不等于完成。结果未知保留原 SID，不重发。
- 索引：任务完成而登记失败时，只检查版本/schema、备份、锁和归属并补登记，不重新调用模型。
- GUI/续聊：到对应项目核对同一个 SID；不预设刷新必然吸纳，不用新会话冒充第二轮，避免同 SID 并发轮次。

个人 profile、采集、会话、日志与索引备份留本地；诊断只取本次必要片段。独立技能包不含源码，不为阅读技能安装另一套运行环境。
