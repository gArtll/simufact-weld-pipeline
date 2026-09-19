# Simufact Welding scripting notes

Simufact Welding 2024.2 · `runscript.bat` · Python 3.10 (bundled) · MARC 2023.4.
Sources: private reference case copies; delivery report file names in brackets.

## Rules

| Rule | Content | Symptom without it | Source |
|---|---|---|---|
| R6′ | `Trajectory.calculate_all_orientations` — new project only, once per trajectory | `trajectory_modification` block absent → no orientation tables, no NDSQ heat source path | 交付报告_A3纯脚本建模_进度.md; 交付报告_A4纯脚本闭环.md |
| R6′ | `calculate_all_projections`, `Trajectory.delete_geometry`, `Project.delete_geometry`, `Trajectory.split_at` — banned | see "Banned methods" | 交付报告_任务C_calculate_all_projections.md; 交付报告_P0-1b焊道单变量对比.md |
| R7 | bead or trajectory setting change: Simufact closed, offline edit of `robots_properties.xml` (Qt qCompress), self-check | no API for orientation mode, variation angle, `connect_to_nodes`; GUI bead regeneration / orientation dialog on a copied project → crash | 交付报告_A3纯脚本建模_进度.md; 交付报告_任务B节点连接.md |
| R17 | `calculate_all_orientations(mode="component-center", search_radius=UnitValueLength(5, "mm"))` on an outer-surface weld line | `FATAL PROGRAM ERROR: ACCESS VIOLATION` (no component inside the default radius); saved `reference_volume` radius equal to the reference case | 交付报告_A4纯脚本闭环.md; 交付报告_B4b_reference_volume.md |
| R18 | weld line CSV header `0;1;0;0` → global-vector; `wl.orientation = "local-vector"` after `import_points` | `RuntimeError: 方向计算不适合全局方向` | 交付报告_A4纯脚本闭环.md; 交付报告_P0_projections.md |

## Banned methods

| Method | Observed | Source |
|---|---|---|
| `Robot/Trajectory.calculate_all_projections()` | weld line points moved to component surface; `connect_to_nodes` true → false; `calculation` → None; `orientation_mode` → Component center; variation angle 45° → 0; weld line back to global; `.dat`: NDSQ node sequences 2 → 0, orientation tables 6 → 0 | 交付报告_任务C_calculate_all_projections.md |
| `calculate_all_projections()` on a global-vector weld line | `RuntimeError: 方向计算不适合全局方向` (robot and trajectory level) | 交付报告_P0_projections.md |
| `calculate_all_orientations` after switching an existing GUI project to local-vector | `ACCESS VIOLATION` | 交付报告_任务C_calculate_all_projections.md |
| `Trajectory.delete_geometry(obj or name)` | `ValueError: 未分配几何至轨迹` with the geometry listed in `all_geometries` | 交付报告_P0-1b焊道单变量对比.md |
| `Project.delete_geometry(name)` on a mounted bead | returns; next `import_geometry` → `ACCESS VIOLATION` | 交付报告_P0-1b焊道单变量对比.md |
| `Trajectory.split_at` | bead assignment and orientation settings cleared | project hard rule 4 |

## Verified calls

```python
g = proj.import_geometry(bead_bdf, unit="mm", as_single_body=True)
wl = proj.new_weld_line(name=...); wl.import_points(csv, field_separator=";", length_unit="mm")
wl.orientation = "local-vector"                                                        # R18
tra = robot.new_trajectory(wl); tra.new_geometry(g)
robot.assign_heat_source_parameter(hs, True)
tra.calculate_all_orientations(mode="component-center", search_radius=UnitValueLength(5, "mm"))  # R6′ / R17
fn = proc.new_fixed_nodes(name=..., fixed_directions=("x",))
fn.import_nodes(csv, decimal_separator=".", field_separator=";", unit="mm", search_radius=0.01)
proc.process_parameters.import_all_settings(xml); proc.check_model(); proc.write_program_input(); proc.start_analysis()
```

Also used: `new_project`, `new_process(process_type="arc-welding")`, `import_material`, `new_component`, `new_temperature`, `import_heat_source_parameters`, `new_bearing`, `new_clamping`.

## Result export (ArcToolCmd)

`ArcToolCmd.exe FileIn=<x.ARC> FileOut=<x.csv> Format=4 [Post=A,B]`. Without `Post=` every post value of the file is
written, which is how the list below was taken (Simufact Welding 2024.2, a welding result file of a component).
Names are the solver's; only `TEMPTURE` is used by the compare step, the others by `post`. Values are in solver units
(SI: m, Pa, K); the node coordinate block is in m and is converted to mm on reading (`run/arccsv.py`).

| Group | Post values |
|---|---|
| temperature | `TEMPTURE`, `PKTEMP` (per-node peak kept by the solver), `TEMPGRDX/Y/Z`, `EXTHEAT`, `EXTFILM`, `HFLUX-X/Y/Z`, `HTC-CB` |
| displacement | `TOTDISP`, `XDIS`, `YDIS`, `ZDIS`, `ARCDISX/Y/Z` |
| stress | `EFFSTS` (equivalent), `TXX`, `TYY`, `TZZ`, `TXY`, `TYZ`, `TZX`, `NORSTS`, `NORSTSX/Y/Z`, `FLWSTRES` |
| strain / damage | `EFFPLS` (equivalent plastic), `EFSTRT`, `EQELS`, `NDDM` |
| contact / mesh | `CONTACT`, `GLUE`, `ELERROR`, `AREACHG` |

CSV layout, and the four values that precede the node values of every block (two ranges; the reader skips them):
`run/arccsv.py`. `PKTEMP` matters for a peak temperature. Measured, not assumed: in a short run where every result increment
was exported, `PKTEMP` and the peak of the exported history agreed to a fraction of a kelvin; in a long run exported
at every hundredth increment the exported peak missed more than half of it. So `PKTEMP` does not depend on the
export interval and a peak read off exported increments does — `post` reports the difference as
`peak_missed_by_sampling_K`.

## No API (offline XML)

| Setting | File | Tool |
|---|---|---|
| trajectory orientation mode, variation angle / offset, `connect_to_nodes` | `<Proc>/robots_properties.xml` (Qt qCompress: 4-byte big-endian length + zlib, base64) | `build/patch_trajectory.py`, `common/qxml.py` |
| contact table pairs | `<Proc>/_contact_table/properties.xml` + `contact_table/user_defined=true` | `build/contact_table_gen.py` + `build/rulesets/*.yaml` |
| virtual pins | — | — |

## Hard rules

| # | Rule |
|---|---|
| 1 | disposable copies only; `open_project()` re-saves the whole project |
| 2 | `_Run_` present → process read-only (`check_model`, `write_program_input`, `start_analysis`) |
| 3 | ASCII paths only for the script runner |
| 4 | no `Trajectory.split_at` |
| 5 | no GUI bead regeneration / orientation dialog on a copied project |
| 6 | success = `.sts` / `.out` exit number (3004); `launch_status.json`, `analysis.wait()`, runscript exit code unreliable |
| 7 | cooling maximum time step non-automatic (default grows to 25 s) |
| 8 | fixed node CSV and weld line CSV end with a newline |
| 9 | units mm: `import_geometry(unit="mm")`, `import_points(length_unit="mm")`; tooling surface ARC in m |
| 10 | `.dat` input check after every change |
| R5 | `open_project` re-save removes the two `nodal_connection_result` fields; `.dat` unchanged |
| R9 | `_Results_`, `_watch_tmp` moved out before a solve |
