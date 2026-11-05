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
- 名称与父级是必需信息；只保留 description 一个说明文本字段，缺失时留空，不编造。按需在说明中表达具体场景与验证行为，不要求固定小标题或套话。
- 工具分配 ID；不使用行号、名称派生 ID，不直接编辑 JSON。

## 从头创建

AI 调用 `begin_create` 登记占位，顺序为组、点、测试点；每次写入带最新 expected_revision。提供 kind/name/parent，说明放在 values.description 内。无代码的目标是待实现，不得伪造绑定。名称和说明完成后，调用 `finish_create` 并传入 `values: {planned: true}`，结束创建动画并保留待实现状态；仅适用于没有任何绑定的规划。已有实现则完成有效绑定后调用普通 `finish_create`。不要用 `resume_operation` 重试绑定校验错误。

实现代码后先 `inspect` 指定相对路径 files。用返回的 start 和 fingerprint 调用 `bind`，values 包含 file/start/fingerprint/role。role 可选 definition、stimulus、check、coverage、rtl。每个位置分配独立 bind ID，目标可以关联多处代码。只有得到绑定成功的返回才能声称已关联。

## 导入已有项目

先读取验证入口及源码，选定明确的文件，调用 inspect；不要递归复制依赖、工具、日志或仿真产物。再调用 import，工具会写入源码 ID 注释并创建三层定义。导入前必须完成下述一对一检查；不能先导入再以实例切换补救。

SV 默认映射：covergroup → group，coverpoint/cross → point，普通 bins/交叉 bins → testpoint。ignore_bins 和 illegal_bins 不导入为待命中的测试点。bin 数组保存为声明模板；隐式 cross 组合不假装已经枚举。扫描得到的是语法定义，不是完整功能语义。

Python 默认映射：class → group，方法/模块函数 → point，assert → testpoint；模块级函数使用文件名分组。复杂层级或非断言形式的测试点用 create + bind 显式管理。装饰器属于函数范围。不要凭测试函数名称推断其全部断言含义。

导入后阅读每个目标代码，用 update 补充合理名称和说明。待实现的行为使用 create，不能把计划写成已实现。

## 稳定绑定及修改

源码注释为 `// @flowcanvas id=bind_<32位hex>` 或 Python `# @flowcanvas id=bind_<32位hex>`。注释紧邻对应完整语法节点。范围由解析器确定，没有结束标识。

保存源码后调用 read 重新校验，不实时增删卡片。bound 表示源码与上次确认版本一致；changed 表示已改变需核对；missing 表示标识/定义消失；conflict 表示重复或类型不符；unverified 表示无法解析，不能使用旧位置。扫描不修改目标名和层级。

内容 changed 时，通过 source 阅读当前完整代码，核对/更新说明后，调用 accept，values 为 `{绑定ID: read返回的fingerprint}`。不能批量盲目 accept 来隐藏警告。重复 ID、解析失败先修复源码再校验。移动文件时目前需人工/AI明确修复绑定，不自动猜测位置。

## 边界

SV 使用 pyslang 的源码语法解析，内置仅对 UVM 注册和日志宏做语法占位；不展开未知覆盖宏，也不证明 elaboration 成功。宏生成的目标要补充真实解析输入或显式维护，不能忽略诊断后伪造成功。

项目内修改可在工作区访问或完全访问模式下进行；路径必须位于当前项目。

## 完成条件

read 确认层级有效、ID 唯一，说明与源码相符，报告未解决的 changed/missing/conflict/unverified。不得把 bound 写成已命中、已通过或已完成回归。


## 运行命中

read 返回的 coverage 是仿真产物的累计投影，与定义、绑定状态分离。由测试用例全局 coverage 配置的提取/合并命令生成，AI 不得填写命中数。当前 UVM 适配按实际 covergroup 实例分别统计，使用源码结构名与编译源码指纹定位；源码变化后需重新构建，不可 accept 旧结果冒充新源码覆盖。bins 数组达标要求成员均满足报告中的 goal；cross 可有自动组合而无显式测试点。命中不等于正确性检查通过。


## 一份定义对应一个实例（强制）

每个覆盖/检查定义只能针对一个验证对象，禁止通过全局实例选择器改变同一目标的含义。发现同一个组件、covergroup 或带测试点的 Python 检查入口被不同接口/DUT/配置复用时，AI 必须复制完整覆盖/检查定义，使用独立类名/函数名及目标 ID，并修改所有实例化或调用位置。复制品先移除原标识，再由 import 分配新 ID；保留的一份可以保留原 ID。底层驱动、无目标标识的通用算法可以共享。同一目标跨运行和用例仍可累计。

SV 导入检查覆盖组件必须有且只有一个显式、固定名称的 factory 创建位置，covergroup 构造一次，拒绝覆盖组件继承复用、已识别的循环创建及动态名称。Python 拒绝已识别的多调用位置、循环调用及参数化目标入口；AI 必须同时检查 fixture、动态调用、工厂覆盖及父级组件复用，静态检查不能证明任意动态程序的唯一性。无法确认时先拆分/改为显式入口，不能声称导入完成。

拆分必须保持采样时序和检查含义，删除不适用于该对象且从未启用的覆盖定义，而不是补假命中。按新结构导入，阅读代码补齐各对象名称和说明，再核对全部绑定。运行报告按完整定义类型检查唯一实例；同类型出现多个实例时拒绝发布累计、页面显示冲突，不能取并集或挑一个实例。更换构建要保留旧记录、开启新累计批次。
