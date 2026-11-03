
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
