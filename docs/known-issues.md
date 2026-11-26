# 当前范围与验收状态

核对日期：2026-11-26。0.2.2卡片布局、隐藏控件和成品资源检查已完成；此前0.2.1测试和表格尺寸保留为[版本历史](cdp-picker-validation.md)。

## 本页卡片与紧凑布局

顶部品牌与设置、按标题/域名选页、紧凑“Chrome / 刷新 / 开启采集 / 断开”工具行和简短状态。“本页控件 · N”可收起，固定视口内滚动卡片；每卡序号、完整换行名称、动作标签、两行用途摘要及编辑/删除/上移/下移。详细说明在编辑弹窗，记录目录和日志放设置。

新布局按实际字体metrics缩放，目标约360dip宽；可用空间不足只缩短列表视口。已覆盖100/125/150/175/200%缩放及175%下14pt字体的隐藏控件检查。

编辑名称/功能说明双必填，定位证据只读；删除保留原始证据与旧包/对话快照。排序、顺序勾选和首轮只交接的语义不变，数据层与app-server机制沿用0.2.1。

## 0.2.2验证范围

| 项目 | 当前状态 |
|---|---|
| 卡片组件 | 6项测试通过，含五档缩放、175%/14pt、200px窄宽、60卡、空态、ID/busy |
| 应用交互 | 24项相关测试通过，含保存后定位新卡 |
| 完整主窗 | 五档工具栏bbox无重叠/越界，展开不超屏幕可用边界，收起尺寸减小 |
| 完整测试 | 152项/21.245秒，151通过、1项Windows真实symlink权限跳过；替代门禁通过 |
| EXE资源/归档 | package-check退出0，v0.2.2 cards/fontAware为true，5项管理入口和资源齐备；归档含capture_list在内8模块、无DesktopBridge |

所有真实Tk控件测试均保持withdrawn，不能写成最终EXE可见窗口验证。本版未新增浏览器/CLI/模型端到端测试。构建输入hash前后一致；EXE检查未打开窗口、连接浏览器或调用模型。

## 资料、顺序与去重

编辑写入.capture-kit/page-state.json覆盖层，load/list/export合并最新annotation；原capture.json、description.md、page/control/createdAt及source.sha256不改。新包携带完整编辑说明，source.annotationEdited标明覆盖。

sequence包含captureIds、ordered、source、executionValidated=false。ordered=false是排列，ordered=true是用户声明步骤；source可为user-declared、capture-order或explicit-selection，是否声明步骤以ordered为准。recording.orderIsWorkflow跟随ordered表示声明步骤，workflowExecutionValidated=false仍表示未实测；它不意味着录制过实际动作。前置与预期结果仍是说明，不是已观测状态或执行授权。

资料、删除后集合、顺序或声明变化必须反映为新的交接内容；完全相同资料才复用原对话。发送仍只读context.md和完整包、简短确认并等待用户，不生成技能/脚本/计划，不触发顺序执行。

## 后台兼容与范围

安装版app-server stdio、选定工作区/会话yolo完全访问与私有索引登记继续沿用；当前适配ZCode3.16.3.8992 / CLI0.17.0。范围约束在交接技能/context.md，权限不扩展首轮任务，不改其他项目或全局模型设置。

协议ACK是受理，模型确认、终态、索引和GUI分别核实。结果不明保留SID不重发，登记失败只补登记。旧task.md/generation/generated仅兼容保留，不读取旧生成任务；页面文字与人的说明作为资料，不作为额外指令。

私有索引不是官方公开API，核对真实会话与版本/schema，备份后窄范围登记；只有本工具ownership及同SID/taskPath匹配旧行可只改mode，外来任务和其他列不动。GUI曾无需重启、激活现有窗口后显示条目，不保证所有build热刷新。

只支持普通主frame DOM；iframe/Shadow DOM/Canvas内部对象不在范围。网页自己的右键处理可能影响采集，声明顺序不等于动作已执行。采集、回收、profile、完整会话、日志和备份留本地，不入Git/Release。
