# 功能点与测试点说明规范

## 阅读目标与栏目

功能点回答“这个功能是什么，要从哪些方面验证”：

1. 功能说明：`summary` 简洁解释行为、接口和约束。
2. 验证思路：`approach` 说明正常、边界、异常场景如何分解。
3. 测试点分解：由真实子目标自动展示，子测试点的 `summary` 解释各自职责。不要重复整份测试点说明。
4. 实现与检查、源码依据及覆盖情况：联系实际定义，指出缺口与证据边界。

测试点回答“这个具体条件怎样被验证”：

1. 验证意图：`summary` 说明针对的规则、边界或风险。
2. 触发条件：`conditions` 写前提、输入、状态和采样条件。
3. 预期结果：`expected` 写正确情况下应该观察到什么，不能写成已通过。
4. 实现与检查：`implementation` 说明激励、采样和判定逻辑，逐项对应源码。
5. 关联用例：`related_cases` 记录真实用例 ID 和关系理由，界面提供跳转。
6. 覆盖情况：运行数据由平台实时展示；`coverage_notes` 解释 bin 的含义、采样边界和覆盖不能证明什么，不保存手写覆盖数字。

功能组只是组织容器，不为其生成详情说明。条目没有证据时保留待补充状态，不能根据名称编造实现。

## 技术说明与证据

- 说明先讲系统角色和行为，再引入源码符号。技术但易读，避免框架术语堆砌。
- 实现条目使用 `title`、`text`、`confidence`、`assignments` 和 `evidence`。平铺展示，不建立嵌套卡片，不机械复制用例时间轴。
- `confidence=source` 必须有真实引用；`inferred` 的推断在正文中明确说明依据和不确定之处。
- `assignments` 含 `expression`、`meaning`、`file`、`line`、可选 `end_line`；表达式必须来自引用范围内的真实源码，不用省略号或伪代码替代。只选理解机制所需的小段代码。
- `evidence` 含 `file`、`line`、可选 `end_line`、`label`，引用实际实现或文档直接相关的源码位置。
- `check_kind` 使用 `unknown`、`coverage_only`、`assertion`、`comparison` 或 `mixed`。只有 coverpoint/cover/bin 采样且没有发现对应断言或比较时，应说明“当前仅有覆盖采样，尚未找到独立正确性检查”。不能把 monitor、打印日志、覆盖命中自动当作判定逻辑。
- 源码说明不代表运行通过；退出码、覆盖命中和正确性结论应区分。

## 关联与更新

先调用 `verification_targets(inspect, ids=[当前目标ID])` 阅读最新目标、绑定、版本、本规范及 `explanation_schema`；必要时通过 `files` / `binding_id` 阅读源码，并用 `test_cases(inspect)` 核对关联用例。

`related_cases` 仅填写当前项目真实存在的 `case_id` 及 `reason`。不能因为同文件、同名字或同模块就断言某用例覆盖当前测试点。无法建立关系时留空并说明缺少证据。

使用已有批量事务：`begin_modify(ids)` → `update(cards=[{id, explanation: {...}}])` → `end_modify(ids)`，每步采用返回的最新 `expected_revision`。保留目标既有名称、层级、源码绑定和用例实现，不直接编辑管理 JSON，不修改源码，不运行测试。不复用其他会话的修改所有权；未实现目标按其现有状态使用 `planned=true` 完成元数据更新。

`source_hashes` 由服务端校验引用后生成，模型不填写。引用代码或绑定源码发生变化时，页面提示重新核对说明。没有源码证据时，应保留推断或待确认，不能补造引用。
