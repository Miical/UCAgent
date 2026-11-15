# 模块概览

模块概览的权威编写规范见 [模块概览编写规范](../src/flowcanvas/guides/module-overview-guide.md)。该文档随插件发布，并由 `module_overview(inspect)` 返回给模型；“生成模块概览”图标发出的 AI 任务要求先读取这份规范再生成。

入口位于设计总览的概览页，复用测试用例的 AI 图标、悬停预览及弹出任务卡。任务发送到当前设计总览专属对话，绑定选中的模块实例。

工具提供 `inspect` / `save`：inspect 返回规范、JSON Schema、源码与版本；save 校验源码引用、真实代码片段、图关系、端口及版本冲突。保存结果自动刷新，主源码版本不匹配时不展示旧概览。
