
# FlowCanvas

UCAgent 的可视化验证工作台插件，支持 Codex 与 OpenCode 原生对话后端。

左侧依次为验证进度、验证目标、测试用例和 Bug 管理；中间为对话；右侧为会话详情与工具活动。两处分隔条可拖动，双击恢复默认比例。默认 Light 主题，左下角可切换 Dark / Light / Graphite。

## 启动

需要已安装并配置的 Codex CLI（`codex login`）或 OpenCode CLI，沿用本机模型与登录配置，不复制凭据。在 UCAgent 仓库根目录安装插件依赖并启动：

```bash
python -m pip install -e ./plugins/FlowCanvas
PYTHONPATH=plugins/FlowCanvas/src:. python -m flowcanvas --port 8810
```

打开 http://127.0.0.1:8810/ 。服务仅绑定本机。

## 对话

- Enter 发送，Shift + Enter 换行；生成期间发送按钮变为停止。
- 顶部切换或新建对话。切换页面不会取消其他会话的生成。
- 模型与推理强度从所选后端的实际目录加载，可在下一轮发送前调整。后端选择在输入框下方；已有消息的对话切换后端会新建对话，保留原历史。
- 响应速度提供标准 / Fast / Ultrafast，默认标准，仅启用模型目录提供的档位。设置随会话保存并在下一轮发送生效；加速会增加用量消耗。标准档明确向 Codex 清除服务档位覆盖。
- 流式展示回复与思考摘要：思考时显示计时，完成后自动收起；思考摘要是否出现取决于模型。
- 连续工具调用合并为步骤组，可展开查看各项输入、输出及成功/失败/停止状态。运行时使用轻微呼吸与旋转动画，遵循系统减少动态效果设置。
- 消息支持复制；上翻历史时不强制滚动，点击“回到最新”继续跟随回复。
- 浏览器断线后自动重连并恢复当前内容。服务重启后保留对话，未完成轮次标记为中断，下次发送时恢复对应后端的原生会话。
- 提供完全访问、工作流执行、只读分析三档权限，默认工作流执行。工作流附件和项目工具由当前对话的 MCP 提供；不自动授权其他 MCP 服务与插件，不提供逐工具审批交互。只读权限不代表隔离读取本机文件。

## 架构与存储

浏览器 → FlowCanvas 会话 API / SSE → 原生后端适配器。Codex 使用 `codex app-server` 的 stdio JSON-RPC；OpenCode 使用独立的 `opencode serve` 进程与 HTTP/SSE。上下文由各后端的原生会话管理，不在每轮重发网页历史。

后端输出归一化为消息、思考摘要、工具活动、轮次状态和用量。前端不依赖原生协议类型；两个后端共用对话、任务卡、工具活动、排队和子 Agent 侧栏。

FlowCanvas 的会话展示记录默认保存在 `~/.local/share/ucagent-flowcanvas/`，可用 `FLOWCANVAS_STATE_DIR` 指定独立目录。每秒保存流式内容、轮次结束立即保存；原生会话历史仍由各后端存储。删除 FlowCanvas 展示记录不会删除原生历史。

实现独立维护在插件内，不修改 UCAgent 核心，不包含 AionUi 源码。Markdown 使用 markdown-it、任务列表扩展及 highlight.js，本地打包，不依赖 CDN。支持标题、多级列表、表格、引用、链接、任务列表、代码高亮与复制代码；流式未闭合代码块也可展示。默认禁用原始 HTML。尚未接入数学公式、Mermaid 和附件。

修改 `frontend/markdown.js` 后，使用 Node.js 20.19+ 执行 `npm ci`、`npm run build`，将生成的 `src/flowcanvas/web/markdown-renderer.js` 一起交付。第三方许可证位于同目录的 `THIRD_PARTY_LICENSES.txt`。普通 Python 启动不需要 Node.js。


### 会话交互

会话菜单支持名称搜索、行内重命名、置顶、归档与恢复，以及当前对话 Markdown 导出。
归档不删除历史；正在运行的会话须先停止才能归档。菜单可统一收起执行详情。
各会话的输入草稿保存在当前浏览器本地，刷新后恢复；发送成功后清除。
选中消息文字可引用到输入框，已有提问可放回输入框修改，两者都保留尚未发送的草稿。
思考、计划和工具详情可复制；连接中断时显示自动重连提示。
快捷键：Cmd/Ctrl + K 打开会话菜单，Cmd/Ctrl + Shift + O 新建对话。
遵循系统减少动态效果设置。以上交互独立实现，没有移植 AionUi 源码。

当前尚未接入附件上传、原生历史分叉、权限审批及交互式提问卡片；这些需要后端协议配套，
不提供无效入口。权限模式可在输入框下方选择，并随会话保存。


### 权限模式

- **完全访问**：Codex 使用 `danger-full-access`，OpenCode 允许原生工具执行，可以直接修改文件、执行命令；不弹出逐命令审批。
- **工作流执行**（默认）：普通对话只读，修改和执行应交由工作流。只有服务端记录的已附加工作流可以执行；文件修改通过该任务的 MCP 工具完成。
- **只读分析**：仅阅读和分析，不修改文件、不运行构建测试、不启动工作流。

权限只允许在两轮之间切换。每轮显式设置后端执行策略，切换模式时重新加载原生会话配置与指导，
从完全访问降级也会覆盖此前的权限。模式选择保存在服务端，不依赖浏览器界面限制。


### OpenCode 适配

已使用本地 OpenCode 1.19.5 验证。程序优先从 PATH 查找 CLI，再检查 `~/.opencode/bin/opencode`；模型、provider 和凭据由 OpenCode 自身读取。适配器不会把 provider 密钥返回浏览器，也不会改写用户的 OpenCode 配置。

已验证范围、诊断记录与剩余边界见 [接入验证记录](docs/opencode-validation.md)。

每个对话独立启动仅监听 `127.0.0.1` 的服务，使用内存中的随机认证口令，并单独连接当前对话的 FlowCanvas MCP。模型目录按对话的工作区读取，包含项目级 `opencode.json` 的配置，只展示已连接且支持工具调用的模型；推理选项使用模型实际提供的 variants，默认档保留原生默认行为。模型偏好按项目和后端分别记忆，已有对话保留自己的选择。没有 Codex Fast/Ultrafast 对应能力的模型隐藏速度选项。

桌面启动未提供代理环境变量时，将操作系统可读取的 HTTP/HTTPS 代理传给 OpenCode 子进程；显式代理环境变量（包括空值）优先保留。本地 OpenCode 和工作流 MCP 连接始终绕过代理，用户已有的 `NO_PROXY` 排除项继续保留。代理信息只放在进程环境中，不写入配置、日志或网页；PAC 脚本不在这项自动适配的范围内。

模型目录使用独立的发现进程读取，自动加载目录不会重启正在执行的对话。手动点击“刷新连接”会刷新目录配置，并在下一轮重新建立当前对话的原生连接，以读取更新后的 provider 配置；原生历史保留。任务或子 Agent 仍在运行时禁止手动重连。

OpenCode 权限属于工具级控制，不是 Codex 的操作系统沙箱。工作流执行与只读分析模式禁止原生 Shell、写入与子任务工具，保留文件读取、搜索和服务端受控的 FlowCanvas MCP。完全访问允许原生工具；交互式 question 工具关闭，提问通过普通对话进行。已配置的外部插件不会加载，其他 MCP 连接会在接受任务前断开。

停止会中断本轮及原生记录中仍在运行的子会话，并回收该对话的执行进程，取消尚未进入执行循环的准备请求。失败终止也会回收执行连接；下次发送或读取历史时重新连接同一原生会话，等待旧进程退出后才建立新连接。子 Agent 侧栏只读取当前父会话的原生后代，不能用任意 session ID 查看其他会话。事件流断开后重连并核对原生消息与状态，本轮剩余输出按消息快照持续更新，避免重连缓冲区中的增量重复拼接；服务重启保留历史与已保存输出，队列不会自动重新执行。工作流发布阶段工具后继续使用现有的目录刷新与同会话恢复流程。

活动轮次中不替换 OpenCode 的 MCP 连接，避免已选中的工具失去连接；新增阶段工具在现有轮次交接时加载。这个处理保留原有的自动续接行为，尚未改成模型开始前预备全部工作流工具。

上下文压缩沿用 OpenCode 原生机制，压缩后的续写仍属于同一次任务。内部摘要和自动续写指令不作为普通聊天内容展示，压缩用量与失败仍正常处理。自定义模型需在原生配置中声明真实的上下文限制；`limit.context` 为零时，原生机制不会按用量阈值提前压缩，适配器不根据模型名称猜测上限。

OpenCode 用量投影的输入数包含缓存读取和写入，输出数包含推理，明细单独保留；事件遗漏时从原生消息快照恢复。`usage.last` 和 `cost` 来自单条原生消息，不是整项任务或会话的累计账单。当前页面尚未展示 token 数；原生费用为零也不代表模型免费。

工作台正常关闭时，先取消 OpenCode 的活动原生任务，再结束服务进程，避免留下它启动的本地命令。部分输出和任务卡中断状态保存到磁盘，排队消息暂停；取消无法确认时记录错误。这项处理不保证远端任务或强制结束工作台进程后的回收。

普通发送和执行中引导遇到接收响应丢失时，按原生消息 ID 查询实际接收记录，不自动重发。确认已接收后继续同一次执行；限定时间内无法确认时，尝试取消任务并关闭连接，保存失败与暂停队列。原生已经报告的完成结果不会被迟到的发送错误或本地等待取消覆盖。

离线回归检查（包含临时工作流宿主的验收与退出响应，不调用模型）：

```bash
PYTHONPATH=plugins/FlowCanvas/src:. python -m unittest discover -s plugins/FlowCanvas/tests -v
```

配置好本地 provider 后，可显式运行真实模型检查（会产生模型调用用量）。文件写入限定在临时工作区，测试创建的原生会话在结束时删除：

```bash
FLOWCANVAS_TEST_OPENCODE=1 PYTHONPATH=plugins/FlowCanvas/src:. python -m unittest discover -s plugins/FlowCanvas/tests -p test_opencode_live.py -v
```

Codex 的真实基线检查覆盖读取工具、排队、任务卡、重连及重启后的同线程续聊。它复用本机登录，只操作临时项目，结束后归档其创建的原生线程：

```bash
FLOWCANVAS_TEST_CODEX=1 PYTHONPATH=plugins/FlowCanvas/src:. python -m unittest discover -s plugins/FlowCanvas/tests -p test_codex_live.py -v
```

协议依据：[OpenCode Server](https://opencode.ai/docs/server/) 与本机 `/doc` 的 OpenAPI 定义；权限差异参见 [OpenCode Permissions](https://opencode.ai/docs/permissions/)。


### 排队、引导与批注

运行中发送的消息进入服务端队列，当前回复结束后按顺序执行。点击“引导”将消息送入当前任务：Codex 使用 `turn/steer`，OpenCode 追加原生 prompt，在下一模型步骤读取，不打断正在执行的工具。失败时保留队列消息。手动停止及服务重启后队列暂停。
停止开始后不再接收新引导；OpenCode 会先等待已经交付中的请求返回，再取消原生执行并回收连接，期间保持停止中。
某个工作流或子会话取消失败时，仍继续停止其余已知子会话和父任务。未确认的取消错误会保存，迟到的完成事件不会将其改成成功，队列保持暂停；下一次明确发起的新任务不继承这次错误。
选中文字可添加多条批注，逐条编辑引用、填写评论或删除；输入框仅展示批注数量。

右栏可通过对话标题旁按钮完全收起，左栏通过当前导航图标收起，均支持缓动过渡。
对话最小宽度为 500px。Logo 返回验证进度与空白对话，保留分栏状态及比例。
Light 为默认主题，主题选择由 FlowCanvas 独立保存。


## RTL 功能简述工作流

`flowcanvas:rtl-summary` 从实验版迁入，配置位于 `src/flowcanvas/workflows/rtl-summary.yaml`。
单阶段读取 RTL、生成一句中文功能摘要，写入 `{OUT}/rtl_summary.md`，记录阶段日志并验收退出。
默认输入为 `{DUT}/{DUT}.v`；使用原生文件工具，后端没有原生读写工具时回退到 UCAgent 文件工具。
不要求登记已读文件，不启用指导文档、Skill 或额外阶段。

安装插件后，通过 UCAgent 启动（路径按实际项目替换）：

```bash
python -m pip install -e ./plugins/FlowCanvas
ucagent /absolute/path/to/workspace MyModule \
  --plugin flowcanvas --plugin-workflow flowcanvas:rtl-summary \
  --backend codex --output summary --loop --exit-on-completion
```

输入文件为 `/absolute/path/to/workspace/MyModule/MyModule.v`，结果在该工作区的
`summary/rtl_summary.md`。网页“＋”支持直接运行，或附加到当前对话后发送消息执行。


## 维护＋号菜单

编辑插件内 `src/flowcanvas/workflow_catalog.yaml`，按文件顺序展示工作流。
每项包含 `id`、`name`、`description`、`workflow` 和 `enabled`；`id` 必须唯一，
`workflow` 引用注册名（如 `flowcanvas:rtl-summary`），`enabled: false` 隐藏该项。
执行逻辑仍在 `workflows/*.yaml`，菜单配置只负责展示。增加流程时先注册，再加入清单。
后端 `/api/chat/workflows` 读取清单，前端打开＋号时刷新列表。支持直接运行到新对话，以及附加到当前对话后执行。


## 对话绑定的工作流执行

附加工作流后由当前对话执行；仅菜单“直接运行”创建新的可见对话。
没有隐藏执行对话、第二个模型执行器或后台代理。

- `run_workflow(workflow_id, rtl_file, dut)` 启动 UCAgent MCP，返回卡片、任务指导和工具清单。
- `run_workflow(card_id)` 恢复同一卡片；MCP 存活时复用，停止时在原工作目录恢复进度并重启。
- 工作流启动后读取实际 MCP `tools/list`，发布原始名称、说明和参数结构；`ReadTextFile`、`Complete` 等直接使用原始参数，不接受额外 card_id 包装。工具绑定最近启动或恢复的卡片；不会自行启动或重启进程。
- `list_workflow_runs()` 查看当前对话绑定的全部卡片、MCP 索引、存活状态、阶段和结果。

每次启动、恢复和工具调用都检查当前对话权限及附加白名单。原生文件访问在工作流模式下只读。
MCP 宿主进程创建后才创建卡片，记录实例 ID、PID 和所属对话。重启沿用卡片 ID，更新 MCP 实例索引。
对话结束或 MCP 停止时未验收的卡片显示暂停；只有 UCAgent 的完成回执能将卡片标为完成。
卡片持久保存，多次运行分别保留。停止对话不会产生后台模型工作；工作台关闭会停止其 MCP 进程。
恢复沿用工作目录和 UCAgent 已保存的阶段检查点，不保证恢复进程死亡时尚未保存的工具内部操作。
当前工作流输入仍限单个 RTL 文件。
Codex 只对本插件创建的会话 MCP 设置 `default_tools_approval_mode="approve"`，授权由服务端逐次检查。

菜单包含 RTL 功能简述和 UCAgent 默认验证流程。默认入口使用空覆盖配置，继承安装版本的默认阶段，不复制维护默认 YAML。默认验证流程需要完整 DUT 验证环境；当前单文件 RTL 启动适配不等价于完整环境准备。

Codex 的完全访问使用 danger-full-access，工作流执行和只读分析使用 read-only；OpenCode 使用前述工具权限控制。
工作流 MCP 仍逐次检查对话模式和附加白名单。提示词不写入随模式变化的读写/构建禁令。
Codex 旧原生线程首次恢复时只做一次旧提示词策略退役通知；之后权限切换只更新沙箱，不注入模式指令。

工具发现不再依赖入口 YAML 的 selected_tools。FlowCanvas 发布实际工具清单后刷新所选后端的 MCP 连接（Codex 使用 `config/mcpServer/reload`，OpenCode 重新注册当前对话的 MCP），并保存发现结果供同一对话重新连接时加载；每次执行仍检查授权与 MCP 存活状态。


## 项目管理

左侧底部的文件夹按钮浏览工作台服务器上的目录（不上传文件）。选择目录后，
已有 `.flowcanvas/project.json` 的项目直接打开；没有该文件则先确认项目名称，
点击“导入并打开”后创建管理文件。导入已有 RTL 时，通过 slang 扫描模块候选，选择顶层后生成依赖清单，不执行综合或仿真。
管理文件包含 `schema_version`、`id`、`name` 和 `created_at`，不写入绝对目录。
最近项目和当前选择保存在用户状态目录的 `projects/state.json`。

新对话绑定创建时的项目目录，并作为后端工作目录。切换项目后列表仅显示该项目的
对话和工作流，正在运行的旧对话不会被迁移或中断。尚未绑定项目的历史对话不会自动迁入。
工作流仍使用独立运行目录，项目选择不改变现有工作流的单 RTL 输入要求。

项目选择是可选的：启动时不强制弹窗；未打开项目时可直接对话。
项目窗口提供“新建项目”，在当前浏览的父目录下创建同名文件夹并初始化管理文件；
“不打开项目，直接对话”关闭当前项目视图，返回未绑定项目的对话。

顶层输入支持模块名或文件路径筛选，候选显示文件和行号。选择后预览依赖源码；
导入时重新解析，并将顶层、文件顺序、头文件及 include 目录保存在 `project.json` 的 `rtl` 字段，
同时生成 `.flowcanvas/rtl.f`（相对路径以项目根目录为基准）。新建空项目无需提供顶层。
扫描基于 pyslang 11，递归处理模块实例和 package 引用，跳过版本控制、虚拟环境等目录。
解析错误、缺失或重名依赖阻止导入，不自动猜测实现。此入口尚未提供自定义宏和条件编译配置；
有这些要求的项目需先补充配置支持。filelist 是静态依赖清单，不代表已经通过完整编译验证。


## RTL 可视化

“验证进度”嵌入仓库内的 rtl-buddy-sch v0.15.0 查看器源码，不使用旧工作台修改版。
上游来源、版本及源码包 SHA-256 记录在 `frontend/rtl-viewer/UPSTREAM.json`。

- Vue 组件、样式与布局：`frontend/rtl-viewer/src/`。
- Python 解析器依赖：`rtl-buddy-sch==0.15.0`，随插件安装。
- FlowCanvas 数据适配：`src/flowcanvas/rtl_viewer.py`。
- 构建脚本：`scripts/build_rtl_viewer.py`。

在插件目录运行 `npm run build:rtl`，安装锁定的前端依赖、执行 Vite 构建并将产物复制到
`src/flowcanvas/web/rtl-viewer/`。产物不入 Git，打包或首次运行前必须构建；打包时静态资源随 wheel 分发。
`npm run build` 同时构建 Markdown 和 RTL 查看器。开发时可在 `frontend/rtl-viewer/`
运行 `npm run dev`。Python 解析通过子进程调用当前 Python 环境安装的 `rtl-buddy-sch==0.15.0`。
不再内嵌上游 Python 解析器源码。Vue 页面移除了 Hub/WebSocket、跨应用事件同步、模型与测试列表探测、Notebook 和 AXI Performance，主题及配色由 FlowCanvas 管理。图形依赖保留 Vue、Pinia、Graphviz WASM 和 ELK；已移除 Chart.js 和 vue-chartjs。

该版本的 slang 前端仍是占位实现，结构生成使用 Verible。安装
`verible-verilog-syntax` 到 PATH 或用户状态目录的 `tools/bin/`。
适配层读取项目 `.flowcanvas/rtl.f`，将源码路径转换成独立缓存 filelist，调用依赖包的 CLI，
不覆盖项目原文件。上游仅支持简单源码清单，include 目录不会传入它的源级 CST 解析器；
条件编译、复杂宏或头文件定义仍受原版解析器能力限制。此视图不运行综合或仿真。
缓存位于用户状态目录 `rtl_views/`，源码内容或解析器版本变化后刷新会重新生成。
查看器资源挂载于 `/rtl-viewer/`，通过 `?view=` 接入当前项目 JSON。FlowCanvas 外层
在页面加载时自动获取结构，暂未接入覆盖率。BSD-3-Clause 和第三方声明随源码、构建产物保留。

## 验证目标

“验证目标”使用固定三层结构：功能组 → 功能点 → 测试点。权威文件为项目
`.flowcanvas/verification.json`；Pydantic 模型 `flowcanvas.targets.Definition` 是 Schema
来源，工具的 `guide` 操作同时返回 JSON Schema 和 Skill。无代码的规划可以先创建，
描述、条件与预期行为可留空。测试用例与运行结果不混入此树。

页面支持创建/编辑、按指定文件预览和导入、搜索、源码查看与绑定校验。导入会在源码
定义前插入 `@flowcanvas id=bind_…` 注释，使用 pyslang/Python AST 获取范围，行号不作 ID。
扫描不重建已发布的卡片；名称和说明也不会随源码扫描被覆盖。
页面只轮询定义文件的 revision，在发布变更、打开页面或点击“核对代码”时检查绑定，
不监听源码实时重建树。源码位置变化更新定位；内容变化需要核对确认；删除、重复标识
或解析错误会显示失效、冲突或无法校验，不能据此声称覆盖命中或验证通过。

配套 Skill：`src/flowcanvas/skills/verification-targets/SKILL.md`。
对话 MCP 工具 `verification_targets` 提供 guide/read/inspect/source 和
create/import/update/bind/accept。模型先读取 guide；所有修改带 expected_revision，
由服务端校验三层关系、ID 唯一性和项目内路径。写操作目前要求完全访问模式；只读及
工作流执行模式可查询，不因调用此工具而获得额外写权限。工具绑定会话的项目，不能
传入任意项目路径。也可以用 `python -m flowcanvas.targets --project … --request request.json`
调用同一实现。

第一版导入约定：SV covergroup / coverpoint 或 cross / 显式普通 bins；Python class /
函数或方法 / assert，模块函数使用文件名分组。复杂 Python 结构可显式 create + bind。
SV 的 ignore_bins/illegal_bins 不作为正常测试点导入；bin 数组保留模板，隐式 cross
不展开。仅为常见 UVM 注册/日志宏提供语法占位；未支持的宏或解析输入需要补齐，不能
把源码扫描当成完整编译。移动文件尚不自动追踪，标识冲突不会自动猜测修复。
