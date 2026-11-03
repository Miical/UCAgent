---
name: verification-targets
description: 在 FlowCanvas 中规划、导入和维护功能组、功能点、测试点，建立并核对 SV/UVM 或 Python 源码绑定。用于验证目标管理，不用于推断仿真通过或覆盖率命中。
---

# 验证目标管理

通过 `verification_targets` 工具管理当前对话绑定项目。先调用 `operation=guide` 获取本规范和当前 Schema，再 `read` 获取最新 revision。工具权限由服务端判断，不能用改写管理文件的方式绕过拒绝。

## 固定结构

权威文件为项目 `.flowcanvas/verification.json`。Schema 由 `flowcanvas.targets.Definition` 维护，通过 `guide` 返回。

- 功能组 group：独立功能域，ID 前缀 fg。
- 功能点 point：可说明的行为或观察维度，唯一父级为 group，ID 前缀 fp。
- 测试点 testpoint：具体条件下应验证的行为，唯一父级为 point，ID 前缀 tp。
- 测试用例独立于这三层。存在定义不代表已经覆盖，绑定代码不代表验证通过。
- 名称与父级是必需信息；description、condition、expected 缺失时留空，不编造。
- 工具分配 ID；不使用行号、名称派生 ID，不直接编辑 JSON。

## 从头创建

调用 `create`，顺序为组、点、测试点；每次写入带最新 expected_revision。提供 kind/name/parent，说明和条件放在 values 内。无代码的目标是待实现，不得伪造绑定。

实现代码后先 `inspect` 指定相对路径 files。用返回的 start 和 fingerprint 调用 `bind`，values 包含 file/start/fingerprint/role。role 可选 definition、stimulus、check、coverage、rtl。每个位置分配独立 bind ID，目标可以关联多处代码。只有得到绑定成功的返回才能声称已关联。

## 导入已有项目

先读取验证入口及源码，选定明确的文件，调用 inspect；不要递归复制依赖、工具、日志或仿真产物。再调用 import，工具会写入源码 ID 注释并创建三层定义。

SV 默认映射：covergroup → group，coverpoint/cross → point，普通 bins/交叉 bins → testpoint。ignore_bins 和 illegal_bins 不导入为待命中的测试点。bin 数组保存为声明模板；隐式 cross 组合不假装已经枚举。扫描得到的是语法定义，不是完整功能语义。

Python 默认映射：class → group，方法/模块函数 → point，assert → testpoint；模块级函数使用文件名分组。复杂层级或非断言形式的测试点用 create + bind 显式管理。装饰器属于函数范围。不要凭测试函数名称推断其全部断言含义。

导入后阅读每个目标代码，用 update 补充合理名称和说明。待实现的行为使用 create，不能把计划写成已实现。

## 稳定绑定及修改

源码注释为 `// @flowcanvas id=bind_<32位hex>` 或 Python `# @flowcanvas id=bind_<32位hex>`。注释紧邻对应完整语法节点。范围由解析器确定，没有结束标识。

保存源码后调用 read 重新校验，不实时增删卡片。bound 表示源码与上次确认版本一致；changed 表示已改变需核对；missing 表示标识/定义消失；conflict 表示重复或类型不符；unverified 表示无法解析，不能使用旧位置。扫描不修改目标名和层级。

内容 changed 时，通过 source 阅读当前完整代码，核对/更新说明和条件后，调用 accept，values 为 `{绑定ID: read返回的fingerprint}`。不能批量盲目 accept 来隐藏警告。重复 ID、解析失败先修复源码再校验。移动文件时目前需人工/AI明确修复绑定，不自动猜测位置。

## 边界

SV 使用 pyslang 的源码语法解析，内置仅对 UVM 注册和日志宏做语法占位；不展开未知覆盖宏，也不证明 elaboration 成功。宏生成的目标要补充真实解析输入或显式维护，不能忽略诊断后伪造成功。

修改工具当前要求完全访问模式；只读和工作流执行模式下可以查看、定位、解析，但不能通过此通用工具改源码。未经修改权限不要用 shell 绕过。

## 完成条件

read 确认层级有效、ID 唯一，说明与源码相符，报告未解决的 changed/missing/conflict/unverified。不得把 bound 写成已命中、已通过或已完成回归。
