# 维护约定

本仓库仅包含 CDP 网页控件采集器。根目录 Python 模块使用同级导入；不要重新依赖其他知识库的 `tools/`、HTML 或绝对路径。

- 主界面保持小置顶窗口。顶部按“启动 Chrome / 刷新 / 记录 / 日志”→选页→“连接并开启采集 / 关闭”排列；设置和详细编辑按需打开，日志存文件。“本页控件”保持可收起的滚动列表，每个控件只占一行：序号、名称、“编辑”和“⋯”。名称按font.measure计算可用宽度并尾部省略；完整名称、用途与条件只在编辑弹窗查看；新界面不提供允许动作选择，删除和上移/下移放在该项“⋯”菜单。资料目录与ZCode原生项目分别记录；可使用扩展health返回的学习空间对齐，切换资料目录不搬旧数据。Chrome profile独立保留。
- 主窗按实际字体metrics缩放，默认目标约280dip宽；空间不足时先缩短单行列表的滚动视口，保留连接、发送状态、顺序与发送入口。不要锁死像素高度或缩小字体掩盖高DPI裁切；名称保持单行，长说明不撑大主窗。旧360dip宽、大卡片完整换行和两行用途摘要要求已被用户的新布局要求覆盖。
- 发送有独立持久状态区，明确显示准备、处理中与计时/不定进度、完成、已发过、失败、未知以及公开回复回执。进度不伪造百分比或等待；快速去重如实立即显示。同份成功或未知资料在UI缓存中禁发，标注/集合/顺序、任务意图/动作策略或实际连接范围变化才视为新内容；index_failed只补登记。UI缓存不是最终去重保证，后台原有OS工作区锁与内容去重必须保留。“复制交接说明”只复制，不调用模型。
- 启动/复用Chrome后只按指定CDP监听端口的真实owner匹配窗口，读取实际前台状态；系统拒绝激活、未找到窗口或归属不明须明确提示，不激活其他Chrome、不重启共享浏览器，不把端点可达说成窗口已显示。
- 使用本人先登录的固定专用 Google Chrome 与 profile，复用指定 CDP 端点。不要替换为匿名浏览器，不重启共享 Chrome，不导出 Cookie、token 或浏览器资料。
- 端口未启动且系统无 Chrome 时，可从便携包显式准备 Google Stable 的桌面 Chrome for Testing。浏览器全目录与登录资料分开；源码和主 Release ZIP 只含准备入口。绑定本机、原端口占用时显式切换并保存。设置持久化可选的回环 `cdpEndpoint`，旧配置缺省 9222。
- 采集范围是普通主 frame DOM。右键标注保存控件位置与人的用途；页面包只合并同一 origin/path 的已标注卡，不记录全页面事件。默认采集排列不代表流程；仅“按此顺序操作”启用时把sequence作为用户声明前后，executionValidated=false表示采集时尚未实测；声明顺序本身不是实际执行或成功证据，学习任务依当前意图/策略和真实页面证据推进。
- 本页列表支持行内编辑说明及“⋯”菜单中的删除、上移/下移；移动即启用用户声明次序。编辑界面只提供annotation名称/说明/前置条件/预期结果等，不展示动作勾选；旧allowedActions字段可为兼容保留，人写名称不是AX名称；page/control及定位证据只读。删除只移入本地回收区，旧交接包和已发会话保留快照，不回写或销毁。资料/顺序改动必须随最新包发送并正确参与交接去重。
- 0.3.1新包使用taskIntent.mode=learn-and-verify、actionPolicy=infer-from-purpose。人只说明控件用途、前置与预期，不预先勾选read/click/fill；ZCode结合用途与实际控件类型/状态推断动作，先inspect再逐步验证并留证据。旧allowedActions不是新策略的权限开关，也不能凭字段或按钮名字扩大业务范围。缺参数、作用不明或涉及业务提交/删除/付款时，依具体用户任务处理，不编造参数或成功；可继续不依赖缺项的检查。
- 旧包无taskIntent或context-only仍只接收资料；旧learn-and-verify包没有actionPolicy时仍按原allowedActions约定。新推断策略只作用于显式新包，不静默升级旧包；意图、策略和连接范围必须进入后台与UI去重身份，不能误命中旧确认回执。交接请求号仅留内部日志/协议 inputId，不拼进给人看的任务正文，不解释成网页DOM id、targetId或ZCode会话ID。
- 发送给 ZCode 后，任务必须出现在用户指定的可见对话中并能继续。后台 CLI 的独立会话、命令结束或产物存在不能替代该验收；核实实际接入能力，不虚构产品命令或界面行为。
- 技能以本仓库 `skills/` 中的文件交付，不自动安装进 Agent 用户目录或修改默认模型。工作区中可保存本项目的 durable 技能副本供任务引用。
- 不提交 `.local/`、profile、captures、页面包、日志、模型认证、私钥、Cookie、token、EXE 或构建环境。示例使用虚构数据。
- 默认“桌面接入”用随包“网页能力学习”扩展连接Extension Host，由App owner创建/发送/打开/等待原生会话并广播消息，不直写索引，不让独立CLI冒充GUI。首次sessions.write/sessions.fullAccess必须由用户在ZCode原生界面批准；不代点、不绕过。按能力检查，不按版本号白名单，不自动升级/降级；桥未就绪明确失败。
- 扩展health返回稳定native workspace（ctx.extension.dataPath/workspace）。资料目录可独立放Documents，也可“使用 ZCode 学习空间”对齐；不同则prompt绝对引用context，不能把资料目录当项目cwd。资料内captures、包内learning、.handoffs与原生SID/operation关联，原生聊天存储仍由ZCode管理。推荐固定集中管理，移动程序、资料和原生项目分别处理，不复制数据库或改路径冒充迁移。
- 模型目录来自同一ctx.models.list，不临时建session读目录；每次native发送输入传所选精确modelID，属于会话请求覆盖，不改全局provider/模型/凭据。配置只持久非秘密选择。桥发现文件只由本地连接器读取，token不进prompt、文档、日志、部署记录或Git。
- 本机配置位于%LOCALAPPDATA%/CDP-Control-Picker/settings.json，只存路径和modelSelection{providerId,modelId}或null；坏配置不自动覆盖，须设置“保存并完成”明确确认。资料目录优先当前--workspace、已保存、已有旧目录，最后Documents/CDP-Picker-Learning；“使用 ZCode 学习空间”只显式切换到health目录，不搬旧资料/profile。默认“跟随后端当前模型”，目录只从真实Composer catalog取。
- 原生createSession创建并单次首发，返回真实SID/operation立即持久native-processing，再openSession并wait精确operation；处理中/完成/失败持续保留。会话创建、受理、导航回执、GUI可见与实时广播各自核实，不凭记录已写承诺直播。unknown保留SID/operation查询，不造新会话重发，不用别轮消息代替本轮结果；模型完成但导航未确认仍明确失败，重复只回原回执，不重新首发或擅自重新打开。
- 部署任务读`skills/cdp-picker-deploy/SKILL.md`与其desktop-connection参考；给人看`docs/deployment.html`。首次设置“安装连接桥”后须在ZCode命令中心“重新加载扩展”一次，再“连接并授权”；不重启App、不代原生授权，日常不重复。外部CLI文件变更不被GUI自动监听，不承诺首次零App配置。下载与依赖核对官方来源及对应commit；不把截图占位伪装真实验收。
- 独立CLI/app-server stdio、旧索引适配仅保留兼容诊断/历史。CLI留空优先PATH zcode-cli/zcode，再回退捆绑CLI；安全解析shell=False，仅捆绑入口用Electron Node。版本用于记录，--version不识别仍实际检查协议/session/list；不保存异常原始stdout/stderr。该诊断不发模型/建会话/写索引，不能代替原生桌面直播。
- 仅显式CLI/index兼容路线使用CDP_PICKER_DESKTOP_EXE、CDP_PICKER_DESKTOP_INDEX、CDP_PICKER_CLI_DATABASE等当前进程覆盖，最后一项只供核对不改变CLI写库。按必要字段/约束和真实身份检查，写前备份；只改本工具ownership对应记录，不影响外来任务。默认原生路线不要求用户配置索引路径。
- 用户保留本工具任务的完全访问授权，模式不扩大业务范围；不改其他项目、Plaza角色或全局模型/认证。恢复保留真实SID与原生项目身份，不新建会话冒充续聊。
- 源码修改先运行相关纯单元测试与语法检查。GUI、浏览器、冻结 EXE 与模型实测分别报告；自动审批拒绝后不得换入口绕过。新学习链路可在虚构的隔离本地页面进行运行器及后台模型验收，不把本地演示当作用户真实业务已通过；不重启共享Chrome、不擅自测试生产业务写入。
- 构建使用根目录 `build.py`，打包图标、采集与任务JS、四份技能及无GUI CDP任务入口；采集器自身无需用户另装运行环境，外部CLI依所选发行形态准备。源码与图标入 Git，二进制和便携包进入明确授权的发布流程；不把旧仓发布误报为新仓新版已验证。
