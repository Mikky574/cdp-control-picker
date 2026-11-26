---
name: cdp-picker-handoff
description: 配置悬浮控件采集器，读取同页标注包或兼容单卡资料，经安装版 app-server、桌面私有索引登记与 sessionId 续聊整理页面能力草稿。适用于采集配置和 AI 交接；采集不授予业务操作权限。
---

# 页面标注整理与会话交接

当前主路径为安装版 ZCode app-server stdio 与桌面私有索引兼容登记。用户明确授权本采集器任务使用 `mode=yolo` 完全访问；不修改全局默认、模型认证或其他 Plaza 角色。适配目标为 ZCode 3.16.3.8992 / 内置 CLI 0.17.0，发布和真实验证状态以随包记录为准。

## 按需读取

- 配置与排错：读 [configuration.md](references/configuration.md)，页面身份与定位按 [cdp-page-contract](../cdp-page-contract/SKILL.md)。
- 整理页面包或续聊：读 [handoff.md](references/handoff.md)，再实际读取用户指定的 `task.md`、完整 `page-bundle.json` 和必要技能。
- 单卡仍可读取 `captures/cap-.../{task.md,capture.json,description.md}`，不强制转包。

## 关键约定

- 点击“整理本页标注（ZCode）”只合并已保存的同页卡片。只采普通主 frame；DOM/AX 观察与人的用途保留来源，卡片顺序不代表流程，同页不代表同一业务对象。
- 任务输出为当前 `generated/` 的 draft skill、页面约定与候选 runner。允许格式、引用和语法检查；整理任务不连接网站、不执行业务动作、不运行生成的 runner、不自动安装技能。缺参数或关系写待确认。
- yolo 是本任务的执行权限，draft 是本任务的产物范围。按任务使用可用工具完成必要校验，未执行项如实报告。
- 后台运行参考 Plaza 的 runtime、持久 SID 与事件订阅方式。绑定时保存真实 sessionId，订阅进度并等待任务终态；session/send ACK 仅表示受理，不能作为完成证据。启动、刷新、标注本身不调用模型。
- 私有索引登记是用户选择的兼容写入，不是官方公开 API。按当前适配器核对版本/schema、真实会话与备份后窄范围登记。仅本工具 owned-marker、同 SID/taskPath 匹配的旧行允许只改 mode；其他列和外来任务保持原样，不自行迁移或删库。
- 同 session 继续；悬浮窗没有续聊输入框，用户在 ZCode 同项目打开原会话继续。task ACK、任务完成、索引登记、GUI 可见与第二轮须分别证明。
- 已有会话而索引失败时保留 SID，只补登记；结果不明不重发，不以换 bundle 编号绕过内容去重。`zcode_runner.py` 的 CLI `-p` 与可选 mode 保留作源码 fallback，不是按钮主路径。

报告输入包、控件数、产物、实际 SID、任务终态、登记与已验证/未验证层级。凭据、个人采集、完整会话、日志和索引备份留在本地，不提交 Git。
