# 采集、原生桌面接入与资料位置

默认“桌面接入”使用随包“网页能力学习”扩展。首次设置“安装连接桥”后，在ZCode命令中心“重新加载扩展”一次，再“连接并授权”；用户在原生界面批准sessions.write/sessions.fullAccess，不自动代点或绕过，不重启App，日常不重复。按真实能力检测，不用版本号白名单；能力不支持明确报告，不偷偷升级或切CLI。

## 路径与模型

| 项目 | 约定 |
|---|---|
| CDP | 本包connection.endpoint是浏览器端点，不是桌面桥或业务URL |
| Chrome | 固定专用profile，由本人登录，复用指定页签 |
| 资料目录 | 保存captures、.capture-kit、page-bundles及包内learning、.handoffs、.logs |
| ZCode学习项目 | 扩展health给出的稳定native workspace，由原生owner维护 |
| 目录对齐 | 可以“使用 ZCode 学习空间”，也可把资料放Documents；不同则prompt绝对引用context，不能把资料目录当native cwd |
| 模型 | 同一桌面ctx.models.list真实catalog，native发送输入传精确modelID；不写凭据或改全局默认 |
| 身份 | 原生SID关联本次operation；handoff编号和Chrome targetId不能替代它 |

推荐固定学习项目与资料目录集中管理。切换资料目录不搬旧资料，移动程序不迁移原生项目/聊天库；迁移必须核对实际身份，不复制数据库试错。设置保存在%LOCALAPPDATA%/CDP-Control-Picker/settings.json，只含路径和modelSelection{providerId,modelId}或null；“保存并完成”后沿用，不含Key/token。当前--workspace优先已存目录，再保留已有旧目录，最后使用Documents/CDP-Picker-Learning；可创建新目录但不搬旧资料。默认“跟随后端当前模型”，显式选择从真实目录刷新；部署JSON模板仍不是程序自动配置。

## 人的操作

连接扩展并完成首次原生授权，核对实际学习空间和模型；再刷新明确选页、开启采集。右键分别保存名称和用途，前置/预期可选，动作由新策略按用途推断。单行编辑/更多菜单保留；复制只复制，不自动提交。

合包按origin/path或file路径，运行时另核对精确targetId及完整URL摘要。主frame DOM适用，不假称支持iframe/Shadow DOM/Canvas内部对象。

## 本页标注与顺序

“本页控件”可展开/收起，每项只占一行：序号、名称、“编辑”和“⋯”。详情在编辑弹窗，删除和排序在更多菜单。名称与用途必填，前置条件/预期结果可选；名称最多120字、其他文本字段最多1500字，页面地址与定位证据只读。旧allowedActions兼容保留、编辑时不改旧值：新infer-from-purpose包不把它作为动作门槛，旧无策略学习包仍按旧规则；字段本身不授予业务权限。编辑存于.capture-kit/page-state.json覆盖层，load/list/export合并最新annotation；原capture.json、description.md、page/control/createdAt和原始文件SHA不改，不把手写名称当作AX名称。删除需确认，仅移入本地回收区；旧包和已发会话保留快照。

默认createdAt升序，新卡追加。上移/下移调整本页持久顺序并勾选“按此顺序操作”；也可取消勾选，只保留排列。勾选只声明人的步骤意图，不代表已验证或已经执行。发送携带sequence，按[交接字段说明](handoff.md)解释。

数据接口为list_page_captures(page)、page_sequence(page)、set_page_sequence(page,capture_ids,ordered=True)、update_capture(id,annotation)、delete_capture(id)，恢复接口restore_capture(id)。set_page_sequence须完整且恰好覆盖本页所有活动卡；update只接受annotation，description非空，preconditions等可为字符串或字符串列表。不要通过这些接口改定位证据、跨页排序或删除旧交接快照。

## 原生会话与排错

原生createSession创建并单次首发，返回真实SID/operation即持久报告native-processing，再openSession并wait精确operation终态；状态与公开回执持久保留。桥可用、会话创建、ACK、导航回执、GUI可见和实时同步各自验证，不把“登记已写”当直播。桥未就绪不报GUI成功，unknown不创建新会话重发。

CLI app-server stdio和旧索引适配是兼容路线，默认不直写桌面索引；CLI的版本未知可继续协议检查，真实能力不足仍失败。CLI连通不代表桌面接入，不能以独立CLI结束或索引行替代原生GUI会话。

桥发现文件含本地认证信息，不交给模型读取，不复制token到日志/任务/配置记录。账号/API key仍在ZCode受支持入口配置。实际安装、模型、工作区与同步细节见[部署参考](../../cdp-picker-deploy/references/desktop-connection.md)，验收以当前发布记录为准。
