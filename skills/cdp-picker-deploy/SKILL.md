---
name: cdp-picker-deploy
description: 部署或迁移 Windows 控件采集器，准备专用 Chrome 和本机 CDP，接入 ZCode 原生桌面扩展，核对资料目录、模型、授权与独立 CLI。用于安装和连接。
---

# 部署与环境对齐

目标是给出可复现的部署记录、可使用的启动入口和分层验收结果。先查当前机器与所选版本，再选择安装或修复路径；不要把示例路径写成用户已有安装。

使用时先把Release附带的技能ZIP解压，保留各技能目录及内部文件；对部署AI只发一句：**“先读 `C:\Deployment\skills\cdp-picker-deploy\SKILL.md`，按当前环境部署或修复。”** 把示例改为解压后的实际绝对路径，即可显式采用，不必全局安装。若用户明确要求自动发现，再按所选版本的[官方技能说明](https://github.com/zai-org/ZCode/blob/v3.18.2/apps/zcode-cli/docs/usage/08-skills.md)安装到其支持的用户或项目 `.zcode/skills` / `.agents/skills`；本次核查同级 `.zcode` 优先于 `.agents`，同名技能可能遮蔽。用该版本支持的 `skills list --cwd <项目目录> --json`核对实际来源，重启进程验证；不要自行写入Agent用户目录，也不把文件存在当作已加载。

- 原生桌面首次接入、固定学习项目、资料目录、对话位置与模型：先读 [desktop-connection.md](references/desktop-connection.md)。默认由扩展通过App owner创建/打开/等待原生会话；首次原生权限由用户批准，不能绕过。不直写索引，也不让CLI兼容诊断冒充桌面接入。
- 下载安装或CLI源码构建：读 [install.md](references/install.md)。官方资料核查日期为 **2026-11-26**；版本与入口可能变化，实际安装前重新核查。
- 没有 Chrome、没有 9222、迁移后端口或程序位置变化：读 [chrome-cdp.md](references/chrome-cdp.md)。先核对现有端点，再选择系统 Chrome 或便携包的完整桌面 Chrome for Testing。
- 盘点 EXE、CLI、Node、数据与工作区：读 [environment.md](references/environment.md)，填写 [部署记录模板](references/deployment-record.example.json)。模板不是主程序自动配置。
- 更换安装位置或 CLI：读 [relocate.md](references/relocate.md)。保留原会话库与工作区身份；不要靠复制数据库、重建会话或修改索引绕过不兼容。
- 检测、失败定位与验收：读 [verification.md](references/verification.md)。版本、stdio、模型、桌面索引、GUI 与 Chrome 分别记录，不能相互替代。

CLI兼容诊断需要固定Node与脚本时，可运行本技能的 [启动器生成器](scripts/prepare_deployment.py)。它只校验路径并生成三个CMD，不安装扩展、不绕过原生授权、不执行生成文件、不修改系统PATH、HOME或默认模型。JSON记录模板不是应用自动配置。原生独立CLI无需包装为Node bundle。

CLI兼容入口优先PATH的`zcode-cli`、`zcode`，再回退桌面捆绑入口，显式配置优先。`zcode.cmd`只解析Node/脚本参数，不执行其中的SET；这条stdio路线不提供桌面原生直播。版本用于记录，真实协议决定连通。

按原生Extension Host能力检查，不从版本号推定可用，不要求升级或降级。CLI/index兼容路线另核对协议、必要结构和真实身份；其历史结果不能代替当前扩展安装、授权、导航和GUI同步验收。桥未就绪明确报告，不静默切CLI或写数据库制造“已接入”。

用户选择的任务权限与业务范围分别遵守。部署和无模型检测不授权额外模型请求或网站动作；凭据只由用户在受支持入口配置，不把 Key、token、Cookie、私钥或完整 provider 文件写入部署记录、启动器、日志或 Git。已有明确安装/测试授权时继续完成，不重复申请确认。

交付时列出实际选中入口及版本、路径记录、已完成的检测层级与未验收项。保持当前采集器任务语义，不在部署中偷偷把旧 context-only 包升级为学习执行。
