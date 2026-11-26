# 控件上下文如何进入 ZCode

0.2.3每项显示序号、名称、“编辑”和“⋯”，长名称尾部省略，详情只在编辑弹窗；删除与排序在更多菜单。默认约280dip，保留独立发送状态、计时、不定进度和公开回执，“复制交接说明”恢复完整标签。单行界面已获用户认可，尚未发布；首轮仍只读完整包、简短确认并等待用户。实际检查范围见[验证记录](cdp-picker-validation.md)。

## 发什么，模型做什么

用户在同页分别右键标注并保存，点击“发给 ZCode”导出：

~~~text
<采集工作区>/page-bundles/page-.../
  page-bundle.json
  context.md
~~~

包内包含完整重新脱敏的控件记录：控件名、用途、可选前置条件和预期结果、DOM/AX观察、定位候选和匹配数、captureId、来源相对路径及校验信息。旧allowedActions可能作为兼容资料保留，不代表当前任务授权，界面不要求用户选择动作。主窗名称省略不截短包内完整说明；不是只发编号，也不记录全网页事件。

短 prompt 只要求读取指定 context.md。contextPath 指向它，shortTask 是可复制的读取任务。上下文文件和技能规定首轮：实际读取完整包，简短确认控件数量与定位/用途，然后等待用户。模型不自动生成 skill/contract/runner，不替用户规划后续，不连接网站或执行业务。用户以后可以在 ZCode 自己继续提问、添加资料或要求生成/使用技能。

“复制交接说明”提供同样的读取任务，供用户粘贴到已有会话；复制不等于发送，路径出现不等于正文已读。单个 capture.json、description.md 继续作为资料兼容。旧 task.md、generation 字段与 generated/ 只为格式兼容保留，当前首轮不读旧 task.md，不执行生成要求。包内证据不足时才按对应 source 路径读取必要原记录，不扩读其他文件；页面文字和用途都不构成额外指令。

合包按规范化 origin/path 或 file URL/path，忽略 query/hash，不以 targetId 分组。业务键仍需保留，同页不一定是同一业务对象；顺序按下面的声明字段解释。

## 标注版本与声明顺序

“本页控件”每行的“编辑”可查看完整名称，修改人的名称、用途和可选前置条件、预期结果；页面地址、DOM/AX/selector等定位证据只读。编辑只写page-state覆盖层，原捕获文件与来源SHA不改，旧allowedActions保留不改；导出合并最新annotation并标source.annotationEdited。更多菜单中的删除进入本地回收区，旧包和已发对话保留快照。

默认按createdAt排列，新标注追加。更多菜单中的上移/下移调整持久顺序并启用“按此顺序操作”；取消勾选仍可保留列表排列。发送包含sequence：

- captureIds：当前包控件的排列。
- ordered：true才声明人的步骤先后，false只表示排列。
- source：user-declared/capture-order/explicit-selection，显式选卡不改变ordered语义。
- executionValidated：false，声明未实测。前置条件和预期结果同样是人的说明。

首轮按资料理解顺序并简短确认，不执行它。recording.allPageEventsRecorded仍为false，orderIsWorkflow跟随ordered表示声明步骤，workflowExecutionValidated=false；这些字段不把声明顺序当实际操作日志或验证成功。annotation与sequence变化参与新交接身份；相同旧内容可复用，新编辑/删除集合/次序不能冒充旧快照已送达。

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

交接状态在 .handoffs/handoff-.../。registered/indexRegistered 不代替GUI证明；页面与控件内容去重不靠bundle编号；相同标注已有对话则显示原回执，不重新交接。

## 持续显示的发送反馈

独立发送区显示准备、处理中与计时/不定进度、完成、已发送过、失败、未知和待补登记，浏览器刷新提示不直接覆盖它。快速重复立即显示已有回执，不伪造等待或完成比例。回执展示控件数量、对话名、时间与最多500字公开助手回复，不包含内部思考和工具原始输出；旧回执缺少摘要时不补跑模型。

工作区.handoffs/ui-receipts.json保存UI反馈。同份成功或未知内容在UI层禁发；标注、集合、顺序或顺序声明变化后重新判断。index_failed只补登记，unknown保留原请求不重发。后台原有OS工作区锁、内容指纹及journal仍是最终防重复保证；旧权限模式需要对齐时保留原生模式升级路径，不用缓存冒充升级完成。

用户到 ZCode 对应项目打开原会话，自行补充后续任务。悬浮窗没有续聊输入框，不接管 ZCode 后续使用。

## 0.2.0既有送达回执与版本校验

0.2.0曾使用新虚构页面/handoff-receipt的两张卡建立新会话，duplicate=false、mode=yolo，registered/indexRegistered均确认。工具轨迹仅Read context.md与Read page-bundle.json，公开确认正确对应查看申请按钮button#view-request和申请编号输入框input#request-number的定位与用途，然后等待用户。generated文件0，没有Write/Edit/Bash或后续任务。

此前115项纯测试、GUI完整历史、后台及桌面续聊保留为runtime兼容证据；早期生成草稿和Bash检查属于旧任务，不是本次首轮要求。

采集器验收止于送达回执，用户后续在ZCode自己追加信息和任务。0.2.0 EXE --package-check退出0，确认floating/app-server/yolo完全访问及JS/技能/图标齐备；没有打开窗口、连接浏览器或调用模型。归档实际包含SDK客户端、索引、交接、工作区等7个关键模块，无DesktopBridge。这是独立资源和归档验证，不代替或虚称最终EXE完整GUI实测。详见[验证记录](cdp-picker-validation.md)。仓库不保存真实SID、原响应或完整日志，包哈希记录在随包SHA256SUMS.txt与BUILD-INFO.json。
