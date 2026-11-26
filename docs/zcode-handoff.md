# 原生桌面接入、资料与会话

本版默认桌面接入由“网页能力学习”扩展连接ZCode Extension Host。由App owner创建/打开/发送/等待会话并广播原生消息；主路径不从独立CLI冒充GUI，不直接写桌面索引。首次设置点“安装连接桥”，到ZCode命令中心执行一次“重新加载扩展”，再“连接并授权”；无需重启App，不能跳过原生授权。

## 原生连接

首次用户在ZCode原生界面批准sessions.write/sessions.fullAccess；不可自动代点或绕过。按能力检查，不从版本号推定兼容，不自动升级或降级。桥health成功后取得稳定native workspace，模型来自同一ctx.models.list，不为目录读取新建session。

发现文件位于当前用户的本地desktop-bridges目录，只由连接器读取。认证token不进入说明、prompt、日志、部署记录或Git；模型不得读取发现文件。桥未就绪明确失败，不静默切CLI或宣称GUI成功。

## 资料目录与项目cwd

扩展专属项目cwd是ctx.extension.dataPath/workspace；采集资料可在Documents等固定目录，也可使用health返回学习空间。不同是有效配置：context以资料实际绝对路径引用，不能将资料目录当native cwd。

captures、.capture-kit、page-bundles及包内learning、.handoffs和.logs集中在资料目录。原生聊天正文与身份仍由ZCode管理，不能把资料目录当作完整聊天备份。切换资料目录不移动旧记录；迁移程序、资料和原生项目分别核对，不复制数据库或批量改cwd绕过身份。

## 会话与状态

1. health/能力/原生授权先确认；不为读取catalog临时建session。
2. createSession由Host创建并单次首发，返回真实SID和operation；输入传所选模型映射的精确native ID，不修改全局provider或凭据。
3. 立即持久报告native-processing/nativeAccepted，再openSession；uiOpened按实际导航回执，不凭SID存在当成功。
4. wait精确operation（SID、commandId与epoch/revision）；只有该operation.status=completed且导航确认才算完整成功，外层wait完成或其他轮次消息不够。

原生owner负责消息/状态同步，但该build是否热加载、即时导航和实时显示仍需实际验收。没有这些证据不承诺直播。unknown保留SID/operation先查询，不新建会话重发；同份已提交资料按真实回执去重。模型完成但导航未确认时会明确失败，保留nativeCompleted和原SID；重复发送只回原回执，不重新首发，也不承诺自动重新打开。可在ZCode查看/打开原会话。模型/原生项目/连接范围进入任务身份，改模型只影响下一次显式点击发送，不自动调用。

## 完整包与任务语义

context.md是实际入口，完整读取page-bundle.json所有控件、人的用途、sequence、connection、runtime和技能引用；路径不是正文已加载。handoff编号只追踪交接，不是网页ID或原生SID。

首条prompt保留任务范围与绝对context，同时显示网页标题/脱敏地址和最多12项控件名称/用途摘要（每项截断，整条不超过3500字）。摘要不替代完整包；只有ordered严格为true且顺序ID完整有效时才称为用户声明次序，否则是控件罗列。本地conversationTitle只是采集器任务标签，不是强制GUI重命名RPC，不能仅按标题猜会话。

新learn-and-verify包使用actionPolicy=infer-from-purpose，依据用途和实时控件推断动作，旧allowedActions不作新包权限开关；旧无策略学习包仍按原约定，旧context-only或无意图只接收确认。意图/策略/连接范围参与去重，不凭旧确认回执挡住学习任务。

先实际inspect再逐步动作与回读，业务参数/作用不明具体说明。连接、定位、流程成功分开，learning文件必须关联真实工具证据。原包executionValidated=false保留原采集事实，不回写成已执行。

## CLI兼容诊断

独立CLI留空优先PATH的zcode-cli/zcode，再回退桌面bundle，显式入口安全解析。仅捆绑入口用Electron Node；独立CLI无需它。版本未知仍实际检查app-server stdio/session/list，无模型/新会话/索引写入；这是本地CLI诊断，不是原生桌面接入或实时消息证明。

旧CLI与私有索引适配的结果仅在显式兼容路线上使用，依协议、结构能力和本地身份核对；不让索引登记冒充GUI可见。当前主路与历史证据见[验证记录](cdp-picker-validation.md)，部署见[图解](deployment.html)。

## 查看与关闭采集器

当前native已确认SID时，主发送区显示“在 ZCode 查看”，可运行中或完成后显式open原会话；不create/send/model、不改原历史回执。

nativeAccepted且本机处理中回执已落盘后，关闭采集器只断开本地CDP，不终止由ZCode拥有的任务；安装中、legacy或未接纳发送仍等待。ZCode和Chrome保持运行，停止任务应在ZCode处理。请求yolo只说明请求的权限，不能冒充nativePermissionVerified真实回读。

无GUI --desktop-check仅health/models，不请求授权、创建会话、打开界面或调用模型；--desktop-bridge用于明确选择实例。此检测通过不替代真实安装、授权、导航或GUI同步。
