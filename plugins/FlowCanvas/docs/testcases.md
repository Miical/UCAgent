
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

运行记录和 stdout/stderr 存在 `.flowcanvas/test-runs/`；右侧「输出」页每 500ms 增量读取，可切换历史记录。刷新不丢日志。命令退出 0 仅显示「命令完成」，不等价于测试通过。每项目最多一个运行，避免共享构建目录相互覆盖。服务退出终止本地进程组并标记中断；远程 SSH 命令是否退出取决于远端脚本，重启服务不会自动恢复或重试命令。

## 项目级 coverage 配置

`coverage` 保存在项目级，每张用例卡片独立保存 `runner`。覆盖率默认 null（未接入），不影响测试运行。AI 通过 `test_cases(operation="configure_coverage", expected_revision=..., values={"coverage": {...}})` 配置；设置 null 清除配置。与其他写操作一致，需要完全访问和匹配 revision。

配置字段为 tool、description、artifact_path、extract_command、merge_command、report_path、parser、build_id_command。artifact_path 必须包含 `{run_dir}`；合并命令必须使用 `{inputs_manifest}`（本批次输入数据库路径的 JSON 数组文件）和 `{merge_dir}`；report_path 必须位于声明的合并目录模板中。运行结束后执行提取和合并命令，命令失败或报告无效时保留上次累计结果。配置校验不等价于实际工具验证。

parser 当前声明为 `line-coverage-json-v1`，目标报告格式为 `{"schema_version":1,"build_id":"...","instances":[{"path":"top.u0","module":"M","files":[{"path":"rtl/M.sv","covered_lines":[10],"uncovered_lines":[11]}]}]}`。行号从 1 开始，互不重叠，文件路径对应实际构建源码；工具原生覆盖点的合并应由工具完成，百分比不能直接相加。提取/合并脚本需将工具报告规范化为此格式；当前 VCS 适配器同时提供实例自身 covered/total 统计（使用 URG 口径，非源码行数简单相加）和 partial_lines；含部分命中点的行单列。

build_id_command 必须针对实际执行位置和编译输入，不能仅使用本地 Git commit。description 记录工具版本、远程位置、同步边界和采集前置条件。未核实命令、产物或解析适配时保持 null，不填占位命令冒充可用配置。


## uvm-cache / Redmi 已接入版本

`flowcanvas.vcs_coverage` 适配远端 VCS W-2024.09-SP1，通过 `--build-dir` 选择远端构建，默认 `build/simv`，当前实验配置使用 `.flowcanvas/build-single-instance/simv`，不自动同步或重新编译。每次运行写入远端 `.flowcanvas/runs/<run_id>`；复制覆盖率静态设计数据但排除旧 testdata，采集使用独立数据库和名称。URG 只合并当前批次的独立数据库，原始产物保留。

模块报告解析针对本项目每个模块一个实例的结构；不支持的多实例报告会拒绝，不能将模块合计冒充实例覆盖率。实际构建 Cache.v 的 SHA256 与本地源码比较，不匹配时在验证进度页标注远端版本。页面展示模块自身统计，不将父模块和子模块重复累计。

累计状态在 `.flowcanvas/coverage/state.json`，每次合并先写独立目录再发布状态；运行 ID 去重。清空使用新 epoch，历史记录保留，旧批次迟到结果不计入。构建 ID 不兼容时拒绝混合，需清空开始新批次。当前仍维持项目级串行运行，尚未开放并行。


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
`.flowcanvas/coverage/database/coverage.vdb`，由 FlowCanvas 统一调用 URG 提取，
更新行覆盖率、翻转覆盖率和功能覆盖率；项目不再维护专属解析脚本或提取命令。
服务也会自动监测外部命令更新的同一数据库，等待写入稳定后刷新面板。

DUT 范围、实际构建输入和功能卡片映射放在固定的 `coverage/config.json`、
`coverage/build.json`、`coverage/targets.json`；可选的编译数据库放在
`coverage/database/compile.vdb`。详细字段和失败边界见 [coverage.md](coverage.md)。

导入时只完整执行一个短冒烟用例。它复用正式环境和平台提取器，使用私有数据库，
验证行数据与功能卡片映射，不污染正式累计；其余导入用例不逐个运行。
清空累计删除运行数据库，保留编译元数据、配置和映射。旧命令式提供方仍可继续使用，
但不享受 VCS 原生数据库自动监测；重新导入可切换到平台内置方式。
