# 当前范围与验收状态

核对日期：2026-11-26。适用版本：v0.2.0。

## 只打通控件上下文与对话

“发给 ZCode”把当前页已保存的控件合包。模型首轮读取完整 page-bundle.json 的定位与用途，简短确认后等待用户。用户在 ZCode 自己添加信息和后续任务；采集器不自动生成技能、页面约定、脚本或计划。

“复制交接说明”只复制读取 context.md 的短任务，不提交模型。完整资料留在文件，范围约束在技能与 context.md，短 prompt 只指向交接任务。

用户保留所选工作区/会话 yolo 完全访问；首轮不连接网站、不操作控件、不执行业务命令。后续由用户自行要求生成/使用技能，属于 ZCode 对话中的新任务，不是采集器首轮流程。

## 验收分层

| 层级 | 当前事实 |
|---|---|
| 最新上下文交接首轮 | 已通过：新虚构页两卡，新会话duplicate=false，mode=yolo，registered/indexRegistered；只读context.md与完整包并确认后等待 |
| 最新冻结 EXE | --package-check退出0；floating/app-server/yolo完全访问，JS/技能/图标均存在；不打开窗口、不连浏览器、不调用模型 |
| 冻结归档 | 实际包含app-server客户端、索引、交接、工作区等7个关键模块，无DesktopBridge |
| 完整纯单元测试 | 120项全部通过 |
| 两卡采集 | 真实可见 Chrome 保存订单号输入框/查询按钮两卡，测试输入值未落盘，业务点击0 |
| 后台与权限 | 原 SID 恢复 edit→yolo/planEnabled=false，元数据步骤模型用量增量0；同 SID 后台续聊与 Bash 校验曾通过 |
| owned 索引 | 有备份，目标旧行只变 mode，其他列未变 |
| 原生 ZCode GUI | 曾在现有窗口显示任务，打开完整历史，桌面同会话可见回复完成，模式“完全访问” |

最新首轮工具轨迹仅两次Read：context.md、page-bundle.json。回复正确确认查看申请按钮button#view-request、申请编号输入框input#request-number的定位与用途后等待用户；generated内文件0，没有Write/Edit/Bash或后续任务。旧的自动生成草稿任务和命令校验只记录为历史运行证据，不是最新首轮任务。完整摘要见[验证记录](cdp-picker-validation.md)，不提交真实 SID、原始响应或完整日志。

包的 contextPath 指向 context.md，shortTask 只要求读取它并确认。旧 task.md、generation 字段及 generated/ 为格式兼容保留，当前首轮不读取旧 task.md、不执行其生成要求；页面文案与用途都是资料，不是额外指令。

最新冻结EXE完成的是独立资源与归档检查，不将其描述为完整GUI链路复测。包内哈希以SHA256SUMS.txt/BUILD-INFO.json为准。

## 后台、索引与刷新

主路径为安装版 app-server stdio，参考 [Plaza runtime/SID/订阅机制](plaza-zcode-reference.md)。协议 ACK 是受理，不表示模型已读正文或已确认；需本轮终态和同 SID 最终回复。相同标注已有对话则复用，不重新交接。结果不明保留原记录，不重发；模型确认而索引失败时仅补登记。

私有索引不是官方公开 API。严格核对安装版3.16.3.8992、精确schema与真实原生主会话、目录/删除状态/mode；备份后窄范围写入。仅 ownership、同 SID/taskPath 匹配的本工具旧行可对齐 mode，外来任务及其他列不动，不迁移或删库。

索引回执不自动验证 GUI，guiVisibilityVerified 仍可为 false；既有 GUI 通过来自独立观察。本次曾激活现有窗口而无需重启即可见条目，不保证所有 build 热刷新，也不推断本机必须重启。采集器当前验收止于控件上下文送达回执，后续由用户自行在ZCode继续，不另设后续工作测试。

yolo 保留给本工具选定工作区/会话。恢复旧会话若用 session/setMode，可能保存当前选定工作区的权限偏好，permissionScope 记录范围；不改其他项目、Plaza角色或全局模型设置。未支持的额外反向交互会明确阻塞，不伪造用户答案。

## 采集与资料边界

只支持普通主 frame DOM；iframe、Shadow DOM、Canvas 内部对象不在范围。网页右键逻辑可能影响采集。同页不等于同一业务对象，卡片顺序不等于流程。

个人采集、profile、凭据、完整会话、日志及索引备份留本地，不入 Git/Release。
