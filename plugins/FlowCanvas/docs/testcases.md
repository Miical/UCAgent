
# 测试用例定义与导入

测试用例页复用验证目标的列表、搜索、展开和源码查看组件。一条定义对应一个测试类或测试函数；定义与仿真结果分开存储，不从源码扫描推断通过、失败或覆盖率。

- UVM：在扫描文件集合内解析直接、间接继承 `uvm_test` 的类，基类也会出现在候选中，不收录 sequence。AI 阅读源码和运行配置后，仅导入具备具体测试激励及独立运行入口的用例，排除只搭建环境的 base test。当前按唯一类名解析继承；包限定的自定义基类、typedef 别名和宏生成的类不保证识别。缺少 include、未知宏及语法错误会显示诊断。解析时只展开已知注册、日志、sequence 操作宏的占位定义，不执行仿真。
- Python：默认 pytest 文件约定 `test_*.py` / `*_test.py`，收录模块级 `test_*` 函数，以及无自定义构造函数的 `Test*` 类中的 `test_*` 方法，支持 async。不收录嵌套帮助函数；参数化函数保留一条定义，不展开运行实例。自定义 pytest 收集规则、继承得到的方法暂不自动收录。
- 扫描预览不改源码。由 AI 显式选择后，在节点前插入 `// @flowcanvas id=bind_…` 或 `# @flowcanvas id=bind_…`。重复导入复用标识；行号由语法范围重新计算。无法解析的文件不导入，修复诊断或缩小扫描集合后重试。
- 唯一定义文件是项目根目录 `.flowcanvas/testcases.json`，包含 schema_version、revision、targets。用例 ID 为 `tc_…`，保存名称、说明、验证条件、预期行为和绑定，不依赖名称或行号作为身份。
- 编辑源码后，重新进入页面或刷新会重新核对绑定；元数据 revision 每 3 秒检查。源码变化不会静默改写定义或自动确认新基线。

AI 使用 `test_cases(operation="guide")` 获取模式，再 read / discover / import / create / update / inspect / bind / source / accept。写操作要求当前对话完全访问，且 expected_revision 匹配。空白项目可先 create，再 inspect / bind；已有项目先 discover，阅读源码和运行入口，随后 import；values.selected 必须逐项提供 file、start、fingerprint、reason、description；description 为 AI 根据源码生成的中文用例说明，包含验证目的、激励和实际检查行为，不能为空，不代表仿真通过。包含基类文件用于解析继承，但不将其选入。页面不提供导入按钮。复用验证目标的绑定校验和完整文件高亮查看。

HTTP 接口为 `/api/chat/projects/{id}/testcases`；GET 读取，POST 使用相同 operation 字段。`/revision` 提供轻量更新检查。定义接口不收集覆盖率或将用例自动关联到验证目标；命令执行使用下述统一入口。

## 统一运行入口

AI 导入前必须通过 `test_cases(operation="configure", expected_revision=..., values={"command": "… {case}", "description": "运行环境和源码同步说明"})` 配置项目全局 runner。它保存在同一 `.flowcanvas/testcases.json` 中，所有用例共用；配置后 revision 增加，导入前需重新 read。

模板由独立 `/bin/bash -lc` 进程执行，工作目录为项目根目录；`{case}` 由源码绑定解析为 UVM 类名或 pytest 文件::类::函数，`{project}` 为项目路径。入口统一进行 shell 转义，模板不要再次给占位符加引号。其他 shell 变量、管道等按 bash 规则执行。模型需根据真实脚本配置编译/运行步骤，不将凭据写入模板；远程模板应明确运行哪个源码副本、是否编译及同步边界。

列表中的运行按钮和 CLI 都通过 `POST /api/chat/projects/{id}/test-runs` 启动，参数为 `case_id`。CLI 用法：

```sh
python -m flowcanvas.test_runner --project /path/to/project --case tc_... --server http://127.0.0.1:8810
```

运行记录和 stdout/stderr 存在 `output/flowcanvas/test-runs/`；右侧「输出」页每 500ms 增量读取，可切换历史记录。刷新不丢日志。命令退出 0 仅显示「命令完成」，不等价于测试通过。每项目最多一个运行，避免共享构建目录相互覆盖。服务退出终止本地进程组并标记中断；远程 SSH 命令是否退出取决于远端脚本，重启服务不会自动恢复或重试命令。

用例列表用绿色、红色、蓝色圆点分别表示最近一次正式运行的“通过 / 失败 / 运行中”，运行中圆点带呼吸动画，悬停查看状态和依据，未运行、无法判定或版本失效时显示浅灰色圆点，悬停区分原因；冒烟不计入正式结果。运行记录的“执行结果”保留判定依据。退出码 0 本身不表示通过，需有无错误的 UVM 结果汇总、明确 TEST PASSED 或 pytest 通过汇总；明确检查错误优先判失败。新运行记录冻结入口、runner 和全部源码绑定的身份，绑定失效或源码/命令更新后，旧结果不作为当前用例标识。缺少完整版本凭证的历史记录仍可查看日志和结果依据，但不显示当前版本的通过标识。

## 项目级 coverage 配置

`coverage` 保存在项目级，每张用例卡片独立保存 `runner`。覆盖率默认 null（未接入），不影响测试运行。AI 通过 `test_cases(operation="configure_coverage", expected_revision=..., values={"coverage": {...}})` 配置；设置 null 清除配置。与其他写操作一致，需要完全访问和匹配 revision。

配置字段为 tool、description、artifact_path、extract_command、merge_command、report_path、parser、build_id_command。artifact_path 必须包含 `{run_dir}`；合并命令必须使用 `{inputs_manifest}`（本批次输入数据库路径的 JSON 数组文件）和 `{merge_dir}`；report_path 必须位于声明的合并目录模板中。运行结束后执行提取和合并命令，命令失败或报告无效时保留上次累计结果。配置校验不等价于实际工具验证。

parser 当前声明为 `line-coverage-json-v1`，目标报告格式为 `{"schema_version":1,"build_id":"...","instances":[{"path":"top.u0","module":"M","files":[{"path":"rtl/M.sv","covered_lines":[10],"uncovered_lines":[11]}]}]}`。行号从 1 开始，互不重叠，文件路径对应实际构建源码；工具原生覆盖点的合并应由工具完成，百分比不能直接相加。提取/合并脚本需将工具报告规范化为此格式；当前 VCS 适配器同时提供实例自身 covered/total 统计（使用 URG 口径，非源码行数简单相加）和 partial_lines；含部分命中点的行单列。

build_id_command 必须针对实际执行位置和编译输入，不能仅使用本地 Git commit。description 记录工具版本、远程位置、同步边界和采集前置条件。未核实命令、产物或解析适配时保持 null，不填占位命令冒充可用配置。


## uvm-cache / Redmi 已接入版本

`flowcanvas.vcs_coverage` 适配远端 VCS W-2024.09-SP1，通过 `--build-dir` 选择远端构建，默认 `build/simv`，当前实验配置使用 `.flowcanvas/build-single-instance/simv`，不自动同步或重新编译。每次运行写入远端 `output/flowcanvas/runs/<run_id>`；复制覆盖率静态设计数据但排除旧 testdata，采集使用独立数据库和名称。URG 只合并当前批次的独立数据库，原始产物保留。

模块报告解析针对本项目每个模块一个实例的结构；不支持的多实例报告会拒绝，不能将模块合计冒充实例覆盖率。实际构建 Cache.v 的 SHA256 与本地源码比较，不匹配时在验证进度页标注远端版本。页面展示模块自身统计，不将父模块和子模块重复累计。

累计状态在 `output/flowcanvas/coverage/state.json`，每次合并先写独立目录再发布状态；运行 ID 去重。清空使用新 epoch，历史记录保留，旧批次迟到结果不计入。构建 ID 不兼容时拒绝混合，需清空开始新批次。当前仍维持项目级串行运行，尚未开放并行。


## 验证目标累计命中

同一个覆盖率合并步骤同时执行 URG `line+group`；输出 JSON 增加 `functional`：

```json
{
  "schema_version": 1,
  "groups": [{
    "type": "cache_cpu_cov::request_cg",
    "instance": "uvm_test_top.env.cpu_cov.request_cg",
    "file": "verification/src/components/cache_bus_cov.sv",
    "source_fingerprint": "编译时验证源码的词法指纹",
    "points": [{
      "name": "command_cp",
      "covered": 1,
      "total": 5,
      "bins": [{"name": "read", "hits": 3, "goal": 1}]
    }]
  }]
}
```

上述 bins 仅示意一个成员，真实报告保存全部显式 bin。以工具原生数据库合并次数/命中，绝不相加百分比。报告中每个 covergroup 实例独立保留，每份目标定义只绑定一个实例；同一类型出现多个实例时拒绝累计。CPU、Memory、MMIO 使用独立组件、独立定义和目标 ID，页面没有实例切换。数组 bins 以 URG 的 LRM 名称逐项保留；自动 cross 使用真实 EXPECTED/COVERED 总数，不生成伪造的测试点定义。当前解析器针对 VCS W-2024.09-SP1 的 uvm-cache 文本格式；不支持的压缩 bin 或计数矛盾会拒绝发布。

验证目标使用现有源码绑定 ID 查找 class/covergroup/coverpoint/bin 的结构名，再匹配报告。编译时源码与当前源码词法指纹不一致、绑定失效或待核对时显示数据过期/无数据，不通过名称近似匹配。未产生数据不能判为零命中或验证通过。报告必须使用唯一、跨运行稳定的 covergroup 实例名，当前实验通过 `set_inst_name` 设置 UVM 组件完整路径。

验证目标与行覆盖率共用批次、构建身份和清空入口。清空后迟到结果不加入新批次，旧数据库保留。新构建不与旧构建混合。覆盖率更新版本纳入目标页的轻量轮询，不因累计变化修改验证定义 revision。Python 目标目前可维护源码绑定，但本 VCS 提取器不会为 Python 断言生成命中数据。


## 共享数据库运行模式

VCS 项目使用平台内置 `coverage.provider=vcs`。每张用例仍独立保存 runner，
把 `{coverage_db}`、`{coverage_name}` 传给真实仿真命令。正式运行固定写入
`output/flowcanvas/coverage/database/coverage.vdb`，由 FlowCanvas 统一调用 URG 提取，
更新行覆盖率、翻转覆盖率和功能覆盖率；项目不再维护专属解析脚本或提取命令。
服务也会自动监测外部命令更新的同一数据库，等待写入稳定后刷新面板。

DUT 范围、实际构建输入和功能卡片映射放在固定的 `coverage/config.json`、
`coverage/build.json`、`coverage/targets.json`；可选的编译数据库放在
`coverage/database/compile.vdb`。详细字段和失败边界见 [coverage.md](coverage.md)。

“同步测试用例”支持重复运行：按运行入口、配置选择和稳定源码绑定复用已有 ID，仅补齐缺失用例。未变化的标题、说明、概览、时间轴、绑定和 runner 保持不动；源码或配置变化、有明确问题时才最小修复并记录原因。旧用例和历史运行不自动删除。

首次同步或冒烟凭证失效时，只完整执行一个短冒烟用例。它复用正式环境和平台提取器，使用私有数据库，
验证行数据与功能卡片映射，不污染正式累计；其余用例不逐个运行。重复同步直接复用仍符合当前源码、命令、构建和覆盖配置的有效凭证，不重复创建冒烟用例或重跑。
清空累计删除运行数据库，保留编译元数据、配置和映射。旧命令式提供方仍可继续使用，
但不享受 VCS 原生数据库自动监测；重新导入可切换到平台内置方式。

## Case workbench

The test-case page initially shows only the case list beside ordinary chat.
The workspace has a pinned, non-closeable main-conversation tab followed by open case tabs.
On the first visit to each project or panel, the left sidebar is expanded and the right sidebar is collapsed. Later visits restore that panel's explicit choices; opening waveform view does not expand the sidebar automatically.
Single-clicking a case reuses one italic preview tab; double-clicking the case or its tab
keeps it open. Double-click no longer renames a case. Kept tabs remain open while the
preview slot is reused. Each tab contains its workbench: Overview,
Source, Runs and Waveform tabs on the left, and its embedded dedicated conversation
on the right. A horizontal resize preference controls the split. Selecting the main tab restores its previous conversation or intentional unsent draft, including after refresh; its saved selection is independent of scoped conversation history. Each case keeps its own mounted
inspector, source/overview position, waveform viewer, conversation history and draft while
its tab is open. Closing a tab releases its inspector without deleting the case or its
conversation; closing the active tab selects a neighbor or returns to the main tab.

Project main and scoped conversations are stored together under `.flowcanvas/conversations/` in the bound project; unbound conversations stay in the global user-state directory. Opening/closing projects switches histories and browser drafts without copying them across scopes.

Opening a case performs a read-only binding lookup. The first send lazily creates
its initial conversation with `case_binding={project_id,case_id}` in server metadata.
The dedicated chat's new-conversation button creates another empty history with
the same binding, without starting a model turn. An explicit `draft_id` makes
retries idempotent. Default materialization reuses the latest non-archived history.
The history picker lists only conversations belonging to the current case or design
tab; the main chat lists ordinary conversations. Selecting history preserves the
binding and the active workspace tab, and the browser remembers that selection.
Creating a conversation keeps previous histories and their running turns intact.
Deleting a card does not erase its conversations.

Messages capture the viewed tab, source binding/line selection, selected run and
selected waveform file at send time. The server resolves the case and validates
that source bindings and runs belong to it before freezing the context in the
message. Queueing preserves that snapshot. This reference context grants no
permission to execute tests or modify sources. Current source is distinguished
from historical execution metadata; it is not presented as a historical snapshot.

Runs are filtered by case ID, including labeled isolated smoke executions.
Selecting a run selects its log and waveform artifacts together. Exit status
does not certify assertions or scoreboard checks. Waveforms are inventoried only
within the selected managed execution directory. Supported artifact extensions
are VCD, FST, FSDB, VPD and WLF. The primary VCD/FST artifact opens automatically in the FlowCanvas waveform panel, using the embedded UCAgent Surfer rendering core; its bundled
FST fallback and loading progress are reused. Other formats retain the artifact download API.
Viewing a waveform does not certify correctness. Files elsewhere are not attributed
to the run; configure its runner to keep artifacts under `{run_dir}`.


## 项目运行产物目录

FlowCanvas 的项目运行产物统一放在 `output/flowcanvas/`：`test-runs/` 保存运行元数据、日志及每次运行目录；`coverage/` 保存覆盖率配置、数据库、报告和源码快照；`workflow-runs/` 保存工作流记录与隔离工作目录；`rtl-views/` 保存设计解析缓存；新生成的编译产物放在 `build/`。项目、用例、验证目标和模块概览定义仍保存在 `.flowcanvas/`。旧的项目运行、覆盖率和备份目录在无运行任务时迁移，并保留旧路径兼容链接。

## 每次运行的波形目录

运行模板必须显式开启实际仿真器或 fixture 的波形记录开关，指定 DUT 层级并覆盖脚本默认关闭设置；使用 `{wave_dir}`，由平台创建并展开为本次 `{run_dir}/waveforms` 的绝对路径，与覆盖率数据库路径一样只由平台进行一次 shell 转义。生成、导入用例的工作流须将它接到实际仿真器或 fixture 的波形输出，核对编译调试选项及运行时开关，不能只创建目录。

波形页读取所选运行的产物，自动打开 `{wave_dir}/waves.vcd`，不显示波形文件选择框。工作流配置仿真器直接生成这一份 VCD，不生成中间 VPD 或增加转换步骤。保留仿真失败状态，报告缺失输出，不搬用历史文件，不把波形存在当作测试通过。更新命令只影响后续运行，不补写旧运行产物。运行结束后平台解析主 VCD，检查信号、时间跨度及可读数值，保存文件指纹。导入冒烟必须同时通过覆盖率和波形验收；缺失、空文件、解析失败或验收后文件发生变化均不能完成导入。波形生成失败单独报告，不改变正式运行的仿真退出码。


## 波形面板

默认只嵌入波形绘图核心，隐藏完整查看器的菜单、工具栏、侧边信号树和状态栏。FlowCanvas 提供原生的信号搜索、最多 32 条信号选择、缩放、全程视图和时间单位操作；信号目录由所选用例本次波形的真实声明分页读取。已有 AI 解读时默认显示全部 AI 信号，并高亮第一条解读对应的信号组，否则从真实目录选择少量时钟、复位、握手及其他信号。信号名称保持原样，不添加 AI 前缀或标签；点击名称可单独高亮该信号，复选框只控制是否显示。更多菜单保留完整查看器和 VCD 下载入口。

运行尚未结束时等待完整产物，结束后自动载入。加载及解析失败在面板内提示并可重试；切换用例子标签或运行、关闭查看器时取消旧请求并移除事件监听，旧页面的消息不能控制新查看器。操作消息仅接受同源、当前绘图窗口和受限命令集合，信号与 AI 点位切换继续使用原生时间精度。

## 波形 AI 解读

波形栏的 AI 按钮沿用任务卡的悬停预览和点击固定交互，任务发送到当前用例专属对话，携带选定的运行 ID 和波形路径，不启动测试。无波形时显示禁用按钮。

模型通过 `case_waveform` 的 `inspect` 获取真实信号目录、原生时间边界与保存格式，通过 `values` 分页读取四态信号变化，再用 `save` 保存选取的信号及逐点解释。保存要求匹配 `expected_revision`，核对信号存在、点位范围及点位信号状态；时间使用原生刻度十进制字符串，不使用日志周期号代替。最多选择 32 条信号、16 个点，每个点填写标题、解释、观察或推断标记及相关信号。

记录位于本次运行的 `waveform-analysis.json`，绑定实际波形的 SHA-256。波形变化后旧解读不再应用；更新通过工具完成，不直接编辑管理文件。每条解读的 `points[i].signals` 是该点所需的最小信号组，`analysis.signals` 保存各组的去重并集。波形面板始终保留全部 AI 信号，名称保持源码原样，当前选中的信号组用蓝色波形及名称选中效果高亮，每个解读点位由随视口缩放的独立标注层画出贯穿整个绘图区的竖线与编号；未选中时为浅灰色细线，当前点位为蓝色高亮；不添加会占据信号行的原生 Marker；点位编号和解释保留在下方解读栏，“AI 解读”显示在现有右侧栏，与“专属对话”切换；波形页右侧栏展开后默认显示解读，每条解读始终完整显示解释文字和实际信号值，点击点位时更新对应信号组的高亮，其他 AI 信号继续显示，保留当前缩放比例，仅将视图和游标定位到该点；标注线逐帧跟随原生波形视口。点位状态来自波形，文字解释仍是模型解读，不构成测试通过证明。


## 覆盖率源码标签与共享对话

验证目标中的行、翻转、分支、条件、状态机及断言覆盖率模块支持单击预览源码标签、双击固定；展开和折叠使用行首箭头。此类标签暂时仅提供源码子标签，右侧对话按项目与覆盖率指标绑定；同一指标下切换模块共用对话，发送时附带并校验当前模块、构建源码和可见行。刷新后会恢复标签并重新校验源码指纹。

所有标签页复用同一份对话列表，按“主对话”和“专属对话”分组，支持统一搜索及归档操作。切换标签只更新详情与绑定对话，不改变左侧导航页面、覆盖率指标或模块定位；从列表手动选择对话只切换当前侧栏历史，不改变页面标签。当前标签的查看上下文只发送到与其绑定匹配的对话。

## 设计总览标签

设计总览保留一个项目级标签，提供“概览、源码、覆盖率”三个子标签，默认进入概览。源码定位以模块定义为准，源码页按栏目滚动展示，实例化位置在前、模块定义在后；代码完整展开，不限制高度，由页面统一纵向滚动。实例化语句由语法树定位，展示前后六行上下文并高亮实际语句范围；顶层模块只显示定义。点击左侧设计总览导航只切换导航内容，不自动打开或激活详情标签；选择具体模块或点击已有标签时才切换详情。层级树选择不同模块时更新同一标签，专属对话支持新建和切换多份历史；切换模块本身不新增标签或对话，关闭标签不删除历史。发送消息时附带当前模块、子标签及相关源码或覆盖率选择，后端按项目与 RTL 层级校验。模块概览的内容及生成规范见 [模块概览](module-overviews.md)。


### 设计总览的覆盖率子标签

设计总览依次提供“概览、源码、覆盖率”三个子标签。覆盖率按当前选中的模块展示对应指标及构建源码高亮，支持指标和文件选择，沿用设计专属对话。设计总览内覆盖率列表的源码入口打开本页覆盖率子标签；验证目标内的覆盖率列表则打开独立源码标签。源码仍由覆盖率 API 校验构建指纹，不用当前源码替代历史构建内容；无数据时明确显示空状态。

用例、设计总览、验证目标、Bug 与覆盖率详情统一采用连续的白底折叠栏目，栏目之间无间隙，以细横线和轻微的标题阴影分隔。生成概览或说明的 AI 操作以图标加文字的描边按钮置于内容顶部，保留任务预览；波形解读操作位于解读标题旁。详情代码完整展开，由页面统一纵向滚动；波形查看器保持独立的可用高度，波形解读位于右侧栏，主区域不重复显示解读。

用例源码只绑定强相关的场景代码：以 `definition` 绑定可运行入口，以 `stimulus` 绑定具体的 sequence/virtual sequence；场景独有且理解用例不可缺少的约束、辅助任务或预期检查可补充绑定。读取依赖以理解执行过程，不等于需要将依赖加入源码列表。共享 monitor、driver、sequencer、agent、env、基类、通用记分板、覆盖采集、fixture、RTL 和构建运行脚本不默认关联，不要求填满所有绑定角色；例外应限于不可替代的场景专属片段并说明理由。重新导入保留用例 ID 和强相关绑定，清理当前用例过宽的基础设施绑定，不改其他用例或共享源码。同文件只展示一次，高亮绑定范围。配置绑定能力仍保留给必要例外，标记保留原换行，脚本 shebang 保持首行。
