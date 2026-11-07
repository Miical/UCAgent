# Coverage publication contract

FlowCanvas publishes both kinds of coverage through
`GET /api/chat/projects/{id}/coverage`. The batch contains a `report` with
`schema_version: 1`, `build_id`, `instances` (line coverage) and `functional`.
`coverage-json-v1` names this shared contract; the older
`line-coverage-json-v1` configuration name remains accepted.

## Provider boundary

- A simulator/test runner writes original evidence in its unique `{run_dir}`.
- A provider adapter extracts a normalized single-run artifact.
- The configured merger merges compatible original evidence or normalized
  atomic observations; it must not average percentages.
- FlowCanvas validates the result before atomically publishing it. Failed
  extraction or merging does not replace the last accepted batch.

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
  and positive integer `goal`.

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

New test authoring must save both a shared runner and `configure_coverage`:
`extract_command`, `artifact_path`, `merge_command`, `report_path`,
`build_id_command`, provider description and `parser: coverage-json-v1`.
The run snapshots this configuration. Original/normalized artifacts must be
under `{run_dir}`, merged output under `{merge_dir}`, and the merger takes
`{inputs_manifest}`.

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
