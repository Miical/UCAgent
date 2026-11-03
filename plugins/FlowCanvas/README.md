# FlowCanvas

UCAgent 的可视化验证工作台插件。第一版提供独立 Web 对话界面与 Codex 后端，验证面板暂为空。

左侧依次为验证进度、验证目标、测试用例和 Bug 管理；中间为对话；右侧为会话详情与工具活动。两处分隔条可拖动，双击恢复默认比例。默认 Light 主题，左下角可切换 Dark / Light / Graphite。

## 启动

需要已安装并登录的 Codex CLI（`codex login`），沿用本机登录，不复制凭据。在 UCAgent 仓库根目录运行：

```bash
PYTHONPATH=plugins/FlowCanvas/src:. python -m flowcanvas --port 8810
```

打开 http://127.0.0.1:8810/ 。服务仅绑定本机。

## 对话

- Enter 发送，Shift + Enter 换行；生成期间发送按钮变为停止。
- 顶部切换或新建对话。切换页面不会取消其他会话的生成。
- 模型与推理强度从本机 Codex 实际目录加载，可在下一轮发送前调整。
- 响应速度提供标准 / Fast / Ultrafast，默认标准，仅启用模型目录提供的档位。设置随会话保存并在下一轮发送生效；加速会增加用量消耗。标准档明确向 Codex 清除服务档位覆盖。
- 流式展示回复与思考摘要：思考时显示计时，完成后自动收起；思考摘要是否出现取决于模型。
- 连续工具调用合并为步骤组，可展开查看各项输入、输出及成功/失败/停止状态。运行时使用轻微呼吸与旋转动画，遵循系统减少动态效果设置。
- 消息支持复制；上翻历史时不强制滚动，点击“回到最新”继续跟随回复。
- 浏览器断线后自动重连并恢复当前内容。服务重启后保留对话，未完成轮次标记为中断，下次发送时恢复原生 Codex thread。
- 提供完全访问、工作流执行、只读分析三档权限，默认工作流执行。未接入工作流附件、审批交互或 UCAgent MCP；继承的 MCP 服务与已配置插件不会自动接入。只读权限不代表隔离读取本机文件。

## 架构与存储

浏览器 → FlowCanvas 会话 API / SSE → Codex 适配器 → `codex app-server` 的 stdio JSON-RPC。上下文由原生 thread 管理，不在每轮重发网页历史，也不启动额外的 UCAgent Agent / checker。

后端输出归一化为消息、思考摘要、工具活动、轮次状态和用量。前端不依赖 Codex RPC 类型；后续可新增 OpenCode / LangChain 适配器，目前仅实现 Codex。

FlowCanvas 的会话展示记录默认保存在 `~/.local/share/ucagent-flowcanvas/`，可用 `FLOWCANVAS_STATE_DIR` 指定独立目录。每秒保存流式内容、轮次结束立即保存；Codex 自身的 thread 历史仍由 Codex 存储。删除 FlowCanvas 目录不会删除原生 Codex 历史。

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

- **完全访问**：Codex 使用 `danger-full-access`，可以直接修改文件、执行命令；不弹出逐命令审批。
- **工作流执行**（默认）：普通对话只读，修改和执行应交由工作流。只有服务端记录的已附加工作流可以执行；文件修改通过该任务的 MCP 工具完成。
- **只读分析**：仅阅读和分析，不修改文件、不运行构建测试、不启动工作流。

权限只允许在两轮之间切换。每轮显式设置 sandbox，切换模式时重新加载原生线程配置与指导，
从完全访问降级也会覆盖此前的权限。模式选择保存在服务端，不依赖浏览器界面限制。


### 排队、引导与批注

运行中发送的消息进入服务端队列，当前回复结束后按顺序执行。点击“引导”通过 Codex
`turn/steer` 将指定消息追加到当前任务；失败时保留队列消息。手动停止及服务重启后队列暂停。
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

权限由运行环境执行：完全访问使用 danger-full-access；工作流执行和只读分析使用 read-only。
工作流 MCP 仍逐次检查对话模式和附加白名单。提示词不写入随模式变化的读写/构建禁令。
旧原生线程首次恢复时只做一次旧提示词策略退役通知；之后权限切换只更新沙箱，不注入模式指令。

工具发现不再依赖入口 YAML 的 selected_tools。FlowCanvas 发布实际工具清单后调用 Codex `config/mcpServer/reload`，并保存发现结果供同一对话重新连接时加载；每次执行仍检查授权与 MCP 存活状态。
