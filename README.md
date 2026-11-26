# CDP控件采集器

在自己已登录的Chrome里标注控件用途，交给ZCode学习怎样使用。控件每项一行，详情点“编辑”；发送状态持续保留。

**本轮按 v0.3.1-rc.3 候选预发布交付，App 内版本为 0.3.1。** 新电脑没有 Chrome 时，使用便携包的 Prepare-Chrome.cmd 准备完整桌面 Chrome。原生会话此前已在本机 ZCode 4.0.0 验证，3.18.2 的目标机仍待验收。见 [RC 说明](docs/releases/v0.3.1-rc.3.md)。

源码：[Mikky574/cdp-control-picker](https://github.com/Mikky574/cdp-control-picker)。[0.3.0稳定版Latest](https://github.com/Mikky574/cdp-control-picker/releases/tag/v0.3.0)保留；RC与当前App能力见单独说明，不混用验收结果。

## 第一次怎样接入

1. 设置中点“安装连接桥”，只安装随包“网页能力学习”插件；不会安装/升级ZCode或代你授权。
2. 首次到ZCode命令中心执行“重新加载扩展”一次，无需重启App；再点“连接并授权”，在ZCode原生界面批准会话写入/完全访问。日常不重复安装、重载或重启。
3. 连接成功后，扩展会报告自己的固定学习项目。资料可选Documents等长期目录，也可点“使用 ZCode 学习空间”统一放置；旧资料不自动搬迁。
4. 设置“ZCode 桌面与模型”中点“刷新模型”，选择服务商/本会话模型，或保留“跟随后端当前模型”；最后“保存并完成”。只保存路径与选择，不保存Key/token、不改全局模型。
5. 启动专用Chrome、登录、刷新选页并开启采集；网页右键保存控件名称、用途和预期结果。
6. 每行“编辑”看详情，“⋯”删除/排序。需要先后时勾选“按此顺序操作”，再交给ZCode学习；无需勾选允许动作。

缺少浏览器或 CDP 时，见 [Chrome 与 CDP 部署说明](skills/cdp-picker-deploy/references/chrome-cdp.md)。已有可用端点直接复用，没端点可点「启动 Chrome」为专用窗口建立端点。9222 被其他程序占用时，可改用并保存 9223 等空闲本机端口。

细致说明：[人用部署图解](docs/deployment.html)。给AI部署：[部署技能](skills/cdp-picker-deploy/SKILL.md)。原生桥能力不足时明确提示，不自动升级桌面、不静默改走CLI或写索引。

## 发送后，到哪里找对话

到 ZCode 的扩展专属项目打开本次任务。本机 4.0.0 的任务列表显示项目名为 `cdp-picker-bridge`。发送受理后，采集器请求 ZCode 打开原生会话；模型运行期间可查看首条消息和进度。

首条可见消息会带网页标题、脱敏地址和控件用途摘要，完整资料仍由AI读取页面包。本地任务标签不强制改写ZCode标题，定位以这次打开的原生会话为准。

采集器持久显示处理中、用时、完成/失败/未知和公开回执。原生已接纳并确认会话后，可点“在 ZCode 查看”打开原对话，不重新发送、不改历史回执。原生会话创建、发送受理、导航回执、GUI实际可见及实时消息同步分别验证；没有回执不显示“已打开”，没有观察不承诺该build实时直播。未知结果保留原会话和操作，不再造会话重发。

原生已接纳且本机处理中回执保存后，可以关闭采集器；任务仍由ZCode继续，只释放本地采集连接，Chrome保留。保持ZCode和Chrome运行；安装中或未接纳发送仍需等待，关闭采集器不是中断任务。

## 一个学习项目，两个可能不同的目录

推荐长期使用一个学习项目和一个资料目录，方便集中管理。“学习工作区”页签中的学习/记录目录就是资料目录；新默认是Documents/CDP-Picker-Learning，已有旧目录会保留，设置可保存下次沿用。扩展的专属原生项目有稳定目录；资料目录可相同，也可独立放置。不同也能交接，任务用绝对路径读取实际context，不能把资料目录当作会话cwd。

资料目录保存captures、page-bundles（每包内learning）、.capture-kit、.handoffs和.logs。原生聊天正文由ZCode管理，不保证全部在资料目录。切换资料目录不搬旧资料；搬程序、搬资料和迁移原生项目/会话是三件事。具体迁移边界见部署说明。

## AI怎样理解控件

新包taskIntent.actionPolicy=infer-from-purpose：人写用途，AI结合实际DOM、条件和预期推断读取/填写/点击等步骤。旧allowedActions只兼容保留，不阻挡新策略探索；旧无策略学习包仍按原动作约定，旧context-only仍只接收确认。

先inspect核对精确页面、唯一性、可见和可用，再逐步尝试并回读。缺参数或业务效果不明确时具体说明；填写可能自动保存，名称叫“查询”也不保证只读。学习报告、技能、页面约定和参数化步骤保存在本包learning/，已验证结论必须对应真实工具证据。声明顺序不是已经执行的证明。

## CLI还用来做什么

CLI用于首次插件管理安装，独立CLI也保留兼容诊断；默认留空时优先PATH的zcode-cli/zcode，再回退捆绑CLI，也可指定入口。检测实际app-server stdio/session/list，不发模型、不建会话、不改索引。版本只作记录，真实协议决定连通；版本文字未知不单独拒绝。

桌面ZCode.exe的Electron Node方式仅是捆绑CLI兼容入口，独立CLI不需要它。CLI连通和历史索引登记不能代替原生桌面接入、打开对话或实时同步；这条路线的旧验收单独保留在验证记录。

| 版本 | 主要变化 |
|---|---|
| 0.2.3 | 单行/隐藏动作，首轮只接收上下文 |
| 0.3.0 | 保留单行、Chrome和持续反馈，增加实际学习；动作可选，默认桌面捆绑CLI |
| 0.3.1 | 保留学习、隐藏动作，默认原生桌面扩展接入；独立CLI保留诊断兼容 |

四份技能：[部署](skills/cdp-picker-deploy/SKILL.md)、[交接](skills/cdp-picker-handoff/SKILL.md)、[学习验证](skills/cdp-learn-and-verify/SKILL.md)、[页面约定](skills/cdp-page-contract/SKILL.md)。原理见[学习图解](docs/cdp-learning.html)，范围见[当前限制](docs/known-issues.md)。

普通主frame DOM适用，iframe/Shadow DOM/Canvas内部对象不承诺支持。Chrome与登录由本人维护；标注、profile、认证、桥token、完整会话和日志留本地，不进Git或发布包。原始证据、旧包和旧会话保留快照；未知动作不自动重发。
