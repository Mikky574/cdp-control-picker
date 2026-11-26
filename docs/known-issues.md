# 当前范围与验收状态

核对日期：2026-11-26。0.2.1新增本页标注管理。源码UI、数据与交接已有验证；已发布0.2.0的真实运行证据保留在[历史记录](cdp-picker-validation.md)。

## 本页控件

小悬浮窗可展开/收起“本页控件”，列表有序号、名称、用途。编辑字段为名称、功能说明、允许动作、前置条件、预期结果；名称与功能说明必填，页面地址和定位证据只读。人的名称不是新的AX证据。

删除确认后进入本地回收区，原始证据保留；旧交接包和已发对话不回写。默认采集时间排列、新卡追加。上移/下移会勾选“按此顺序操作”，也可取消；勾选只声明意图先后，未执行验证。

## 0.2.1验证范围

| 项目 | 当前证据 |
|---|---|
| 本页列表与编辑/删除/移动/勾选 | 11项源码UI/管理测试通过：8项fake、3项真实withdrawn Tk控件测试 |
| 编辑/删除/排序保护 | Treeview编辑保存、软删除、上下移动、checkbox、未保存和busy barrier、弹窗回调清理通过 |
| 数据与快照 | 同页隔离、overlay持久化、原捕获/说明/旧bundle不变、7实例并发不丢、损坏state fail-closed通过 |
| 交接与身份 | 15项fake交接测试通过，含编辑/删除/sequence/ordered变化去重及旧包兼容 |
| 完整测试 | 144项：143通过、1项真实symlink因Windows权限跳过；替代最终文件门禁测试通过 |
| EXE资源检查 | --package-check退出0，版本0.2.1、5项controlManagement入口齐备，floating/yolo/JS/技能/图标正确，无窗口/浏览器/模型调用 |
| 构建与归档 | 构建输入hash前后一致；CArchive/PYZ含7个关键模块、无DesktopBridge，app字节码含0.2.1及5个新增管理/生命周期方法 |

隐藏Tk验证使用真实widget但不代表最终EXE窗口的人工预览；本版未新增真实CLI/模型/浏览器或GUI端到端测试。首轮交接仍止于收到上下文，后续由用户在ZCode继续。

## 资料、顺序与去重

编辑写入.capture-kit/page-state.json覆盖层，load/list/export合并最新annotation；原capture.json、description.md、page/control/createdAt及source.sha256不改。新包携带完整编辑说明，source.annotationEdited标明覆盖。

sequence包含captureIds、ordered、source、executionValidated=false。ordered=false是排列，ordered=true是用户声明步骤；source可为user-declared、capture-order或explicit-selection，是否声明步骤以ordered为准。recording.orderIsWorkflow跟随ordered表示声明步骤，workflowExecutionValidated=false仍表示未实测；它不意味着录制过实际动作。前置与预期结果仍是说明，不是已观测状态或执行授权。

资料、删除后集合、顺序或声明变化必须反映为新的交接内容；完全相同资料才复用原对话。发送仍只读context.md和完整包、简短确认并等待用户，不生成技能/脚本/计划，不触发顺序执行。

## 后台兼容与范围

安装版app-server stdio、选定工作区/会话yolo完全访问与私有索引登记继续沿用；当前适配ZCode3.16.3.8992 / CLI0.17.0。范围约束在交接技能/context.md，权限不扩展首轮任务，不改其他项目或全局模型设置。

协议ACK是受理，模型确认、终态、索引和GUI分别核实。结果不明保留SID不重发，登记失败只补登记。旧task.md/generation/generated仅兼容保留，不读取旧生成任务；页面文字与人的说明作为资料，不作为额外指令。

私有索引不是官方公开API，核对真实会话与版本/schema，备份后窄范围登记；只有本工具ownership及同SID/taskPath匹配旧行可只改mode，外来任务和其他列不动。GUI曾无需重启、激活现有窗口后显示条目，不保证所有build热刷新。

只支持普通主frame DOM；iframe/Shadow DOM/Canvas内部对象不在范围。网页自己的右键处理可能影响采集，声明顺序不等于动作已执行。采集、回收、profile、完整会话、日志和备份留本地，不入Git/Release。
