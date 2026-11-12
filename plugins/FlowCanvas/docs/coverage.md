# Coverage publication contract

FlowCanvas publishes code, assertion and functional coverage through
`GET /api/chat/projects/{id}/coverage`. The batch contains a `report` with
`schema_version: 1`, `build_id`, `instances` (line coverage), `code_metrics` and `functional`.
`coverage-json-v1` names this shared contract; the older
`line-coverage-json-v1` configuration name remains accepted.

## Platform-owned VCS ingestion

VCS projects use the built-in native provider, configured with
`test_case_runs(operation=configure, settings={coverage: {provider: vcs,
tool: vcs-urg, description: actual setup}}, expected_revision=...)`.
No project-specific extraction, merge or build identity commands are needed.
The platform invokes the installed URG and converts line/source details, toggle
bit-direction, branch, condition, FSM and assertion atoms, plus functional card
observations through shared readers.

The fixed project layout is:

```
.flowcanvas/coverage/
  config.json                 # provider, native DUT hierarchy, optional URG executable
  build.json                  # compiled inputs used to identify the build
  targets.json                # explicit native functional selectors -> panel bindings
  database/coverage.vdb/       # accumulated runtime observations
  database/compile.vdb/        # optional matching compile metadata (copy or project-local symlink)
  sources/                    # exact source snapshots for panel source viewing
  reports/                    # platform-generated URG/normalized report snapshots
  state.json                  # last accepted panel publication and ingestion state
```

`config.json` contains `provider: vcs`, `dut_instance` (for example `tb.dut`),
and optionally `urg_executable` when URG is not on PATH. The installed URG under
`VCS_HOME/bin/urg` can locate its installation through the configured executable;
licensing environment remains the service's normal installation environment.
The top module and project RTL files come from `.flowcanvas/project.json`.
`build.json` contains `inputs`, a nonempty list of project-relative paths to the
actual compiled binary and compiled design metadata. Directories are hashed
recursively; runtime `testdata` is forbidden in build identity inputs.

`targets.json` is a list of entries containing `target_id`, `file`, compiled
source `sha256`, frozen `binding_fingerprints`, and
`selector: {type, instance?, point?, bin?, excluded?}`. Every selector must identify
one native instance; the reader does not guess compiler-generated suffixes or
merge repeated instances. Excluded native bins remain without measurable goals.
Updating titles does not infer or change selectors. The project supplies metadata,
not executable parser code.

Normal test runners forward `{coverage_db}` and `{coverage_name}` to VCS
`-cm_dir` and `-cm_name`. The service expands these to the fixed runtime database
and a unique run ID. Native coverage must already be enabled at compilation.
A completed managed run triggers immediate platform extraction; an independent
background watcher also detects database updates from external runners. It polls
configured recent projects, debounces changes, avoids active managed/VCS writers,
and checks database/build stability again before publishing. Updates during
extraction are discarded and retried after quiescence. A failed extraction retains
the last accepted report and records `ingestion_error`; it does not publish empty
success data. Identical snapshots are not re-extracted across service restarts.

The browser reads normalized API data, never binary VDB files. Published snapshots
represent the whole native database; they are not summed again. A changed build
is rejected until accumulated coverage is cleared. The native database, run names
and accepted reports remain separate from test assertion outcomes.

Clear accumulated coverage deletes runtime `database/coverage.vdb` and generated
publication directories. It preserves compile metadata, configuration, mappings,
compiled input manifest and source snapshots, so the next simulation can collect
without rebuilding. Historical test logs/artifacts remain. Reset is serialized
with extraction and rejected while managed tests are active.

Coverage smoke runs one short case to natural completion with the same runner as
its reference imported case. It uses a private `{run_dir}/coverage.vdb`, invokes
the same platform extractor, and validates measured line data and functional
observations against panel bindings without publishing formal accumulation.
Its receipt includes native metadata/build identity; changed metadata invalidates
acceptance. Legacy `startup_smoke` only checks startup and cannot satisfy import.

Existing `provider: commands` integrations remain supported. Independent per-run
providers use `mode: per_run` with explicit extraction and merge commands; legacy
shared-command setups also remain readable. They are not automatically watched.
New VCS imports use the native provider rather than generating custom scripts.

`line_status` and `functional.status` are `available` or `unavailable`.
Unavailable data has no observations. A measured zero-hit result has actual
denominators and zero hit counts. Neither means that a test passed.

### Line observations

Each `instances` entry supplies `path`, `module`, `covered`, `total`
and optionally `files`. Native reports can preserve their own aggregate metric.
The LCOV adapter uses `scope: design` and `unit: executable_lines`: LCOV
does not establish per-instance attribution.

Each LCOV file contains project-relative `path`, source `sha256`,
`line_hits` keyed by executable source line, and derived `covered_lines`,
`uncovered_lines`, `partial_lines`. The adapter saves exact source snapshots
under `.flowcanvas/coverage/sources/`. Source viewing checks the hash rather
than fetching a hardcoded project or remote host.

### Functional observations

Legacy SV/UVM adapters may retain `functional.groups`. Their native database
merger remains responsible for exact coverage accumulation.

Python/Toffee uses `functional.observations`:

- `target_id`: an existing stable panel ID.
- `binding_fingerprints`: binding ID → confirmed source fingerprint.
- `bins`: records containing unique `name`, nonnegative integer `hits`
  and positive integer `goal`. Native reports with compressed bin details can instead
  provide exact integer `covered` / `total` and an empty `bins` list. When both are
  present, they must agree. These aggregate observations require native database
  accumulation; the independent-run JSON merger rejects them.

Toffee group/point/bin paths are mapped explicitly to testpoint IDs through a
project JSON object such as
`{"FG-API/FC-ADD/CK-ZERO": "tp_<32 hex characters>"}`.
The adapter derives parent observations from the real panel hierarchy.
It does not infer IDs from display titles, function names or line numbers.
Unknown paths and invalid bindings fail extraction. Stale published bindings
remain visible as stale; their hits are not shown as current coverage.

## Built-in command interface

Run `python -m flowcanvas.coverage_adapter --help` for arguments.

- `identity --project ROOT --inputs-manifest FILE`: hash an explicit JSON list
  of relative RTL, build artifacts, coverage definitions and sampling helpers.
  Do not include secrets. Normally exclude individual test files so separate
  cases can accumulate against the same design/coverage build.
- `extract --adapter toffee --project ROOT --report REPORT --mapping MAP
  --build-id ID --lcov LCOV --module TOP --output ARTIFACT`: read the native
  `toffee_report.json` group/bin hit counters and an LCOV file converted from
  this run's native line database. No DUT or test module is imported.
- `extract --adapter normalized --report REPORT --build-id ID --output ARTIFACT`:
  validate a normalized artifact emitted by another provider.
- `merge --inputs-manifest FILE --output REPORT`: merge normalized line atoms
  and functional bin counters from a single compatible build. Legacy
  aggregate-only/native functional data requires its own database merger.

The built-in Toffee adapter never starts a DUT or test. With --dat-dir it invokes
the installed verilator_coverage converter after the run. The AI must determine
real input paths, filters and report directories; other native formats require
their corresponding conversion step. Use the existing VCS/URG merger when appropriate.

## Creation-time configuration

Each case stores its own runner. VCS import prepares the fixed native metadata
and selects `provider: vcs`; it does not write extraction or build shell commands.
The three import child stages configure cards, runners and coverage separately.
One dedicated short complete coverage smoke validates the real chain before final
review. The scope file records `smoke_case_id`, `smoke_reference_case_id` and
`smoke_run_id`; remaining imported tests are not executed. Passing this check does
not imply full coverage or correctness of every imported case.

For command-based providers the project still supplies `extract_command`,
`artifact_path`, `build_id_command` and (in per_run mode) `merge_command` plus
`report_path`. The build identity must describe the actual executed binary and
coverage definitions, not just newer source files.

`summary/testcase_plan.json` records `coverage_plan` with adapter,
per-run `line_source` / `functional_source`, and project-relative
`mapping_file` / `build_inputs_file`. Authoring checks these files/configuration
without running DUT tests; the first actual run verifies that the collection
chain works. Static configuration acceptance must never be reported as
successful coverage collection.

## Default Verilator collection

The runner exports FLOWCANVAS_RUN_DIR for each execution. SetCoverage must point
to a per-case file below its raw/ directory before the first simulation step.
During fixture teardown, record set_func_coverage and set_line_coverage, and
always Finish the DUT. The run completes before extraction starts.

Use extract --adapter toffee --dat-dir RUN_DIR/raw --module TOP together with
the native Toffee JSON report. The adapter invokes verilator_coverage
--write-info on this run's .dat files; missing inputs or tools are failures, not
zero coverage. --dat-dir and --lcov are mutually exclusive.

Creation requires line_enabled=true, functional_enabled=true and collector_files
in coverage_plan. For the Toffee adapter the static gate checks actual
SetCoverage, set_line_coverage and set_func_coverage calls and a --dat-dir/--lcov
extraction path. These checks verify configuration/source structure, not runtime
success. An unavailable metric in a report is not a waiver for configuring it.

For Toffee, --report accepts the actual JSON file or this run's report directory.
A directory must contain exactly one toffee_report.json, including timestamped
subdirectories. Missing or ambiguous reports fail; the adapter never selects the
latest report from another run or follows a report symlink outside that directory.

## Native metric types and DUT scope

When --dat-dir is supplied, the primary line metric is derived exclusively
from native Verilator v_line records. LCOV generated from a mixed database is
not used to classify metric types. No DUT line records means
line_status=unavailable and line_reason=no_instrumented_dut_lines, not 100%.

code_metrics preserves line/toggle/branch/expr/cond/fsm atoms separately, with
dut and generated scopes. Original project RTL source hashes identify DUT
files even when Picker copies them into a build directory. Without a project
RTL manifest, scope falls back to the explicitly selected top's source stem.
Generated wrappers remain in the breakdown but do not affect the main DUT
line denominator. Native atoms merge only with the same source identity.
Old mixed LCOV batches must be re-extracted from their native databases before
merging with the corrected native-line basis.

Python runner {case} expands to an absolute source path plus pytest node selector.
Changing the working directory does not change this entry. Do not prepend a
project or verification directory to {case}; non-Python class entries retain
their native class name. Shell escaping is performed by the runner.

`flowcanvas.urg_native.read_groups` reads native URG type, source file, instance,
point totals and available bin tables. It never treats elaborated type suffixes as
source declarations or merges multiple instances. Providers still supply explicit
card mappings and verify them against the compiled source snapshot.

### Native code metrics and assertions

The VCS reader requests `line+tgl+branch+cond+fsm+assert+group` from URG.
Branch and condition scores come from actual covered/not-covered detail rows,
including case/default branches and per-instance observations. FSM scores count
transitions and sequences; state details are retained separately because URG
excludes states from the score. Each parsed denominator must agree with native
URG totals; incomplete or unsupported detail tables fail publication instead of
inventing individual hits. The reader targets the installed VCS W-2024.09-SP1
text format; other versions require format validation.

The panel shows overview rings for all seven supported metrics and provides
metric tabs with module,
source and coverage-point details. Missing instrumentation shows no data, never
zero or full coverage. The assertion ratio means properties attempted, and its
separate result table preserves attempts, real successes, failures and incomplete
attempts. An attempted property can still fail. Unresolvable macro-expanded
assertion source lines remain unavailable rather than pointing at a fabricated
line. Cover properties and sequences remain functional coverage constructs;
they do not count as assertion successes.
