# ZCode交接与CDP学习验证

v0.3.0的目标是让ZCode实际理解和尝试使用采集到的页面能力。默认导出的taskIntent为learn-and-verify；保留真实native sessionId、后台app-server stdio、索引版本检查与备份机制。

## 人看到的任务

提示词明确要求学习并验证本页控件，读取工作区内的context.md和指定技能，再输出真实验证结果与可复用能力。不再附加内部handoff号。context.md是本地说明文件，既不是ZCode服务地址，也不是网页定位地址。

requestId只存在本地交接日志及原生inputId；sessionId由ZCode创建并用于桌面对话登记；Chrome targetId来自当前浏览器，DOM id/selector来自控件。这几种身份不能替换。见[编号与流程说明](cdp-learning.md)。

## 实际交给模型的内容

页面包带taskIntent（模式和合同版本）、connection（当前endpoint/targetId/页面身份/完整URL摘要）、runtime.command（本机可执行数组）、两份实际技能引用、完整控件证据及人的用途和顺序。query/hash不直接写入URL，只保留身份校验摘要。

ZCode先完整读取技能与包，执行后台inspect，核对唯一性/可见/可用；按明确用途、参数和顺序尝试获准的只读或可撤销操作，再读回结果。参数不足就具体列出；可能产生业务变更的动作需对应任务授权，不能凭click允许列表扩大范围。

EXE的--cdp-task/--output入口不创建窗口；结果写结构化JSON，不能依赖windowed EXE的stdout或只看退出码。同一个act任务文件有独占执行记录，完成返回旧回执，未知不重发；inspect/read-page可继续观察。调用协议见[学习技能](../skills/cdp-learn-and-verify/SKILL.md)。

## 结果与恢复

learning/保存工具结果、learning-report.json、SKILL.md、page-contract.json和参数化runner.json/调用步骤。连接、定位、流程各自标verified/inferred/blocked并关联工具证据；派发点击不等于业务成功。原始采集包保持未验证，不回写历史快照。

新意图和连接范围进入后台指纹与UI缓存；相同learn任务不重复提交，旧context-only成功回执不阻止新学习任务。协议ACK、native终态、最终快照、索引登记和可见GUI分别核实。未知保留原会话不重发，index_failed只补登记。

没有taskIntent的旧包保留只接收资料的原语义。它们的历史验证不代表本版已执行CDP学习。
