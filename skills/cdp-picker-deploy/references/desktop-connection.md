# 原生桌面接入、学习空间与模型

默认路线是打包的“网页能力学习”扩展接入ZCode Extension Host，由App owner创建/广播真实会话；不是CLI造会话后直写索引。按实际能力检查，不按版本号白名单拒绝；能力不齐明确报告，不偷偷升级桌面或切换到CLI冒充桌面成功。首次安装、原生重载和授权按下节顺序；真实结果以当前验收记录为准。

## 首次安装、重载与授权

1. 核对所选CLI具备插件管理能力、资料目录已存在；设置显式点“安装连接桥”。bridge_setup.py只暂存随包四个资源并通过原生插件CLI校验/安装，不手写全局JSON，不装或升级ZCode、不发模型、不重启App。
2. 首次外部CLI安装完成后，到ZCode命令中心执行“重新加载扩展”一次，然后回设置点“连接并授权”。这是重载扩展，不是退出重启App。日常不重复安装/重载/重启；更新或授权失效按实际提示处理。
3. 用户在ZCode原生界面批准`sessions.write`与`sessions.fullAccess`。不能自动代点、修改grants文件或绕过；有效授权沿用，范围/版本变更需重新确认时如实提示。
4. health成功并返回native workspace才说桥可用；health的只读授权线索不单独证明write/fullAccess，真正以原生授权和创建网关为准。安装回执installed不等于connected，activationNeeded表示还要首次重载，permissionsGranted=false不表示已获权限。

官方默认插件根是`~/.zcode/cli/plugins`，运行配置为`~/.zcode/cli/config.json`；所选CLI必须与当前App同一用户及实际数据基目录，自定义`ZCODE_DATA_BASE_DIR`按[environment.md](environment.md)对齐。换CLI程序位置不等于换App资料身份。插件装了却不可见时，先核对是否同一数据基目录，再原生重载；不复制私库或修改HOME试错。

官方[Extension Host说明](https://github.com/zai-org/ZCode/blob/v3.18.2/docs/plugins/extension-host.md)§16.1说明不监听外部文件变化，插件管理或“重新加载扩展”触发重读；§12.1区分扩展重载与App重启，§17.7约定原生授权。因此不能承诺首次完全零App配置。

AI核查安装时使用同一安全解析CLI argv、同一资料cwd，等价顺序为：

```text
<CLI argv> plugins validate <staged>/cdp-picker-bridge --json
<CLI argv> plugins marketplace add <staged>
<CLI argv> plugins install cdp-picker-bridge@cdp-picker-local --scope user
<CLI argv> plugins enable cdp-picker-bridge@cdp-picker-local
```

`<staged>`是受本工具管理的本地市场副本，默认位于`%LOCALAPPDATA%/CDP-Control-Picker/desktop-connector`；validate必须返回明确成功JSON，仅退出0或Electron日志不算校验通过。每一步失败即报告阶段，不能把非零退出猜成“已经装过”继续，也不盲重置插件目录。本地已有改动应保留，不覆盖。首次流程已由源码和fake测试覆盖，真实安装/授权结果仍按当前验收记录说。

市场名`cdp-picker-local`、插件名`cdp-picker-bridge`，显示名“网页能力学习”。manifest的`engines.zcode=^1.4.0`指Extension Host API版本，sessions=1、rows=4为协议要求，不是桌面3.18产品版本白名单。

模型catalog来自同一扩展的`ctx.models.list`，不为读列表临时建会话；每次native发送输入传精确modelID，不写API key或改全局provider。发现文件只由本地连接器读取，认证token不进说明、prompt、部署记录、日志或Git，不交给模型读取。

## 两个目录

扩展专属项目的稳定native cwd是`ctx.extension.dataPath/workspace`，由ZCode原生维护。采集器资料目录可以放Documents等可写位置，也可选择“使用 ZCode 学习空间”与health返回目录对齐。二者不同是有效配置，必须明确告诉人：资料目录不等于ZCode项目cwd；发送prompt使用本次context的实际绝对路径。

| 位置 | 内容与身份 |
|---|---|
| 资料目录captures、.capture-kit | 原始控件证据及编辑/删除/顺序覆盖层 |
| 资料目录page-bundles | 本次快照、context.md，learning在该包内 |
| 资料目录.handoffs、.logs | 交接绑定、操作状态/回执与诊断；不分享认证和完整私人历史 |
| ZCode专属native workspace | 桌面项目及其会话cwd，health给出实际位置 |
| 原生会话存储 | 由ZCode管理；不假称资料目录包含完整聊天数据库 |
| Chrome profile | 独立持久浏览器资料，不混入技能包或学习产物 |

推荐固定这一学习项目，资料目录也长期使用一个；目录名“网页能力学习”是推荐用途，不强制用户路径。切换资料目录只影响此后的资料与引用，旧标注/包不自动搬迁。移动程序、移动资料和迁移原生项目/会话是三件事；未完成身份核对不能宣称迁移成功，不复制数据库或批量改cwd。

## 发送后对话在哪里

原生`createSession`由Host创建并单次首发，返回真实SID与operation；立即持久报告native-processing/nativeAccepted，再`openSession`导航，随后wait精确operation。资料、SID和operation关联持久记录，不以标题相同代替身份。用户在ZCode的扩展专属项目打开该任务并继续，而不是去CLI终端找一份独立聊天。

首条可见消息附网页标题/脱敏地址和控件摘要，但模型仍必须读完整context与页面包。本地任务标签不通过renameRPC强制GUI标题，定位用原生会话与导航回执；摘要列出的顺序也不等于已执行。

| 回执 | 可说什么 |
|---|---|
| health/能力检查 | 原生桥可用；不表示模型已经运行 |
| createSession ACK/SID/operation | App owner已创建并受理单次首发；不表示模型完成 |
| openSession导航回执 | 原生导航已请求/确认，按回执字段表述；实际GUI可见另观察 |
| wait同operation终态 | 校验SID、commandId及epoch/revision；只有该operation.status=completed才证明模型完成，外层wait完成不够 |
| GUI观察 | 实际看到同一会话、当前消息及状态；不凭桥或索引记录宣称实时直播 |

完整成功还需uiOpened确认；模型已完成但导航未确认时明确失败并保留nativeCompleted/SID。重复发送只回原回执，不重新首发，也不自动重新打开。主窗“在 ZCode 查看”可显式打开已确认的原SID；只导航，不create/send或调用模型，不改历史回执。原生owner广播消息与状态，但具体build的导航、热加载和实时同步必须按实际验收记录说。桥未就绪时不报GUI成功；未知结果保留SID/operation先查询，不再创建会话重发。CLI入口和stdio检测只作为兼容诊断，不替代桌面导航、实时消息或原生会话验收。

## 配置与模型的边界

设置的三个页签为“学习工作区”“ZCode 桌面与模型”“CLI 兼容诊断”；在“学习工作区”选学习/记录目录，在桌面页安装、连接、刷新模型并选择服务商/本会话模型，最后“保存并完成”。默认“跟随后端当前模型”对应modelSelection=null。只保存非秘密选择，模型账号和Key由ZCode原生配置。模型选择是本次会话发送输入，不把选择写入全局模型设置；改变选择不会自动重发已完成或未知任务。providerId/modelId由真实catalog映射到native精确id，不进prompt或个人provider配置。模型、原生项目、连接范围与transport参与去重，改选择只改变下一次显式发送身份，不自动发任务；默认继承以实际UI为准，不猜catalog或静默换模型。

## 本机持久配置与优先级

配置位于`%LOCALAPPDATA%/CDP-Control-Picker/settings.json`，schemaVersion=1，只接受workspaceDir、zcodeCli、chromeExe、desktopBridgeFile路径与modelSelection（null或providerId/modelId）；不保存Key、桥token或provider正文，desktopBridgeFile只是发现文件路径。坏配置保留不自动覆盖，需用户在设置显式“保存并完成”确认重建。

资料目录优先级：当前启动显式`--workspace` > 已保存workspaceDir > 已存在旧目录（冻结EXE旁或源码.local下CDP-Picker-Workspace）> Windows KnownFolder Documents/CDP-Picker-Learning。GUI首次新默认会创建目录并保存非秘密选择；设置可创建新资料目录，但不搬旧数据。点击“使用 ZCode 学习空间”只切到health给出的现有目录，不移动旧资料或Chrome profile。

连接文件留空自动发现；多个活动桌面实例时明确选择连接文件路径，不把文件内容或token贴给模型。“刷新模型”仅取同一桌面Composer目录，默认跟随后端当前模型；显式选择需provider和model都有效。路径与选择可保存下次使用，不代表模型凭据已经配置或请求已执行。
