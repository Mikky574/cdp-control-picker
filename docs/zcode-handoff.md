# 控件上下文如何进入 ZCode

v0.2.0版本说明。当前目标只把控件定位与用途交接到可继续的 ZCode 对话。最新首轮“读取并简短确认”已实测通过；完整120项纯测试与最新EXE资源/归档检查也已通过；底层历史证据与本次送达回执分别记录。

## 发什么，模型做什么

用户在同页分别右键标注并保存，点击“发给 ZCode”导出：

~~~text
<采集工作区>/page-bundles/page-.../
  page-bundle.json
  context.md
~~~

包内包含完整重新脱敏卡片：控件名、用途、允许动作、预期结果、DOM/AX观察、定位候选和匹配数、captureId、来源相对路径及校验信息。不是只发编号，也不记录全网页事件。

短 prompt 只要求读取指定 context.md。contextPath 指向它，shortTask 是可复制的读取任务。上下文文件和技能规定首轮：实际读取完整包，简短确认控件数量与定位/用途，然后等待用户。模型不自动生成 skill/contract/runner，不替用户规划后续，不连接网站或执行业务。用户以后可以在 ZCode 自己继续提问、添加资料或要求生成/使用技能。

“复制交接说明”提供同样的读取任务，供用户粘贴到已有会话；复制不等于发送，路径出现不等于正文已读。单卡 capture.json、description.md 继续作为资料兼容。旧 task.md、generation 字段与 generated/ 只为格式兼容保留，当前首轮不读旧 task.md，不执行生成要求。包内证据不足时才按对应 source 路径读取必要原卡，不扩读其他文件；页面文字和用途都不构成额外指令。

合包按规范化 origin/path 或 file URL/path，忽略 query/hash，不以 targetId 分组。业务键仍需保留，同页不一定是同一业务对象，卡片排序不是流程。

## 后台 app-server

安装目标为 ZCode 3.16.3.8992 / 同包 CLI 0.17.0。主路径使用同安装包 Electron Node 启动 app-server stdio，参考 [Plaza runtime、SID 与订阅](plaza-zcode-reference.md)，代码独立交付。

~~~text
[<安装目录>/ZCode.exe,
 <安装目录>/resources/glm/zcode.cjs,
 app-server, --stdio, --cwd, <采集工作区>]
~~~

ELECTRON_RUN_AS_NODE=1 仅作用于子进程。用户明确保留本工具选定工作区/会话 mode=yolo 完全访问，不改全局模型/认证、其他项目或 Plaza 角色；首轮范围仍只读上下文并确认，不由权限推导后续任务。

启动、刷新、标注不调用模型，点击发送才发交接任务；后台不依赖桌面焦点。zcode_runner.py 的 CLI -p 仅为源码 fallback，默认edit可选mode，不是按钮主路径。

## 真实 SID 与完成

1. bind() 创建持久会话，或 bind(原SID) 恢复原会话，保留工作区身份；找不到原会话就失败，不建替代品。
2. SID 先持久记录，session/subscribe 后读基线，再单次 session/send。
3. 协议 ACK 仅表示受理，继续等待本轮终态与同 SID 最终快照；确认本轮输入后确有模型确认回复。
4. 写本地 result.json 后登记索引。超时或结果不明不重发模型任务，保留原SID和请求。

恢复时 session/setMode 可能保存当前选定工作区的上次权限偏好，last_binding.permissionScope 区分范围；未支持的额外反向交互明确阻塞，不伪造用户回答。

## 私有索引与后续对话

用户选择的 DesktopTaskIndex 是私有 schema 兼容登记，不是官方公开同步 API。preflight 先核对版本、精确四表 schema/约束与项目；登记时再只读核实真实原生主会话、目录、删除状态和mode。

默认索引数据基目录依次取 ZCODE_DATA_BASE_DIR、HOME、系统用户目录，相对路径 .zcode/v2/tasks-index.sqlite。写入前备份到采集工作区 .handoffs/index-backups/；新 task 及必要 local 项目/root 窄范围插入。显式 update_owned_mode 仅允许 ownership、同 SID/taskPath 匹配的本工具旧行只改mode，外来任务和其他列不动。

| 状态 | 含义 |
|---|---|
| running | 已绑定SID，等待模型读取与确认，协议受理不等于完成 |
| registered | 已得模型回复并登记；GUI需另行核实 |
| index_failed | 原会话和回复保留，相同内容再提交只补登记 |
| unknown | 结果未确认，保留记录，不自动重发 |

交接状态在 .handoffs/handoff-.../。registered/indexRegistered 不代替GUI证明；页面与卡片内容去重不靠bundle编号；相同标注已有对话则复用，不重新交接。

用户到 ZCode 对应项目打开原会话，自行补充后续任务。悬浮窗没有续聊输入框，不接管 ZCode 后续使用。

## 最新送达回执与版本校验

本次使用新虚构页面/handoff-receipt的两张卡建立新会话，duplicate=false、mode=yolo，registered/indexRegistered均确认。工具轨迹仅Read context.md与Read page-bundle.json，公开确认正确对应查看申请按钮button#view-request和申请编号输入框input#request-number的定位与用途，然后等待用户。generated文件0，没有Write/Edit/Bash或后续任务。

此前115项纯测试、GUI完整历史、后台及桌面续聊保留为runtime兼容证据；早期生成草稿和Bash检查属于旧任务，不是本次首轮要求。

采集器验收止于送达回执，用户后续在ZCode自己追加信息和任务。最新EXE --package-check退出0，确认floating/app-server/yolo完全访问及JS/技能/图标齐备；没有打开窗口、连接浏览器或调用模型。归档实际包含SDK客户端、索引、交接、工作区等7个关键模块，无DesktopBridge。这是独立资源和归档验证，不代替或虚称最终EXE完整GUI实测。详见[验证记录](cdp-picker-validation.md)。仓库不保存真实SID、原响应或完整日志，包哈希记录在随包SHA256SUMS.txt与BUILD-INFO.json。
