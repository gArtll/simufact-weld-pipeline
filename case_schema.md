# case.json 字段说明

所有路径都相对于 case 文件所在目录。长度单位 mm，时间单位 s。路径必须是纯 ASCII（Simufact 硬规则 3）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_name` | str | 是 | 工程名，同时用作 Simufact 工程名（ASCII） |
| `metadata` | obj | 否 | 实验标签，后处理与对比用，不参与建模：`study_id`（对比实验名）、`mesh_strategy`（如 `uniform` / `weld_refined`）。后处理按本字段区分模型，不靠文件名 |
| `geometry_family` | str | 回归 case | 旧标签 `fillet_web_on_plate` / `fillet_tube_on_plate`，只由回归 case 携带，不参与任何决策；项目 case 的做法由 `inspect` → `plan` 决定 |
| `output_dir` | str | 否 | 输出根目录；为空时用 `config.yaml` 的 `work_root/<case_name>`。下面固定生成 `01_prep … 05_compare` 和 `06_selfcheck` |
| `base.web_bdf` / `base.plate_bdf` | path | 是 | 母材腹板、板 BDF（R1：GRID* + CHEXA） |
| `base.web_name` / `base.plate_name` | str | 是 | Simufact 组件显示名 |
| `base_mesh` | obj | 否 | 仅回归 case：参数化 HyperMesh automesh（固定 X 腹板法向，不读 CAD）；项目 case 用它时 `hm` 拒绝。`00_hm` 成功时 prep/build 用 `00_hm/base_web.bdf`、`base_plate.bdf` 代替 `base.*_bdf` |
| `base_mesh.plate.length_mm` / `.width_mm` / `.thickness_mm` | num | 是 | 板长（Z，自 0 起）、板宽（X，关于 x=0 对称）、板厚（上表面 y=0，向 −Y） |
| `base_mesh.plate.curvature_radius_mm` | num | 否 | 板沿长度方向的曲率半径；0（默认）为平板，否则须大于板长的一半；腹板节点随板面同高度偏移 |
| `base_mesh.web.height_mm` / `.thickness_mm` | num | 是 | 腹板高（+Y）、厚（X，关于 x=0 对称）；腹板法向固定为 X |
| `base_mesh.web.z_start_mm` / `.length_mm` | num | 是（z_start 默认 0） | 腹板沿板长方向的起点和长度，须在板长范围内 |
| `base_mesh.mesh.weld_band_width_mm` | num | 是 | 焊缝带宽度：板上腹板两侧各一条；腹板上自底面起的高度；过渡带宽度与之相同 |
| `base_mesh.mesh.weld_band_size_mm` | num | 是 | 焊缝带单元尺寸；腹板首排单元高度取 max(本值, 5 mm)，保证 prep 焊根线只取腹板底边 |
| `base_mesh.mesh.transition_ratio` | num | 否 | 过渡带尺寸 = 焊缝带尺寸 × 本值（默认 2，不超过远场尺寸） |
| `base_mesh.mesh.far_size_mm` | num | 是 | 远场单元尺寸 |
| `base_mesh.mesh.plate_layers` / `.web_layers` | int | 否 | 板厚方向层数（默认 3）、腹板厚度方向层数（默认 2，preflight PF4 只 warn） |
| `base_mesh.quality.max_penta_fraction` / `.min_scaled_jacobian` | num | 否 | CPENTA 单元占比上限（默认 0.02；是 CPENTA 数 / 单元数，不是楔形长宽比）、缩放雅可比下限（默认 0.3；本项目定义：单元每个角点由三条棱单位向量构成的行列式，取全单元最小值，与 HyperMesh 的 Jacobian 指标不同） |
| `base_mesh.quality.shrink_step_mm` / `.max_retries` | num/int | 否 | CPENTA 占比超限时焊缝带尺寸每次缩小量（默认 0.5）；最多重跑次数（默认 3）。缩放雅可比不达标时改为过渡放大比 −0.5（不低于 1）并把焊缝带宽度加倍一次 |
| `materials.base_xmt` / `materials.filler_xmt` | path | 是 | 母材材料；焊丝材料（挂焊枪，每侧导入一次） |
| `heat_source.xml` | path | 是 | 热源参数 XML |
| `heat_source.entity_name` | str | 是 | 导入后的实体名，build 自检必须相同（R16） |
| `temperature` | obj | 是 | `name, initial_C, convective_htc, contact_htc, emission` |
| `gravity` | [3] | 否 | 默认 `[0, -9.80665, 0]` |
| `bead.leg_mm` | num | 是 | 焊脚 |
| `bead.section_spacing_mm` | num | 是 | 截面间距（R21，按焊根线弧长等距） |
| `bead.micro_snap_mm` | num | 否 | 接触面节点微吸附阈值，默认 0.01；0 表示关闭 |
| `bead.points` | int | 否 | 焊缝线点数，默认 23 |
| `bead.section_frame` | str | 否 | `web_plane`（默认）或 `tube_on_plate`；截面局部坐标规则 |
| `bead.frame` | obj | 否 | `{normal: [3], along: [3]}`：腹板法向与焊缝走向（`web_plane`）。不给时取 `plan/plan.json` 中 `inspect` 量出的接头坐标系；只有无 STEP 的回归 case 才退回旧坐标系 normal=+X、along=+Z。`sides[].side` 的 `neg`/`pos` 指沿 `normal` 的负/正侧 |
| `weld_edge` | obj | 否 | 焊根边规则；闭合圆使用 `mode=closed_circular, closed=true, axis, axis_value_mm, center_mm, radius_mm` |
| `sides[]` | list | 是 | 每个焊接侧一项 |
| `sides[].label` | str | 是 | 标签，例如 `w001`；焊枪名为 `Rob-<label>` |
| `sides[].side` | `neg`/`pos` | 是 | 焊道位于腹板沿接头坐标系 `normal` 的负侧还是正侧面（旧坐标系下即 −X / +X） |
| `sides[].window_csv` | path | 是 | 焊缝窗口定义点（取首末活动点，投影到外表面线，R19） |
| `sides[].line_source` | str | 否 | `outer`（默认，R14）或 `root`（tube_on_plate 参照的显式例外） |
| `sides[].trajectory` | obj | 是 | `calculation, orientation_mode, connect_to_nodes, variation_angle_deg, variation_offset_mm`，build 离线补丁写入 |
| `window_expected_length_mm` | num | 否 | 给出时，prep 自检外表面短线长差 < 0.01 mm |
| `process.parameters_xml` | path | 是 | 工艺参数 XML（`import_all_settings`）；冷却段最大步长必须非自动（硬规则 7） |
| `process.weld_speed_mm_s` | num | 是 | 焊速，用于温度对比截面定位 |
| `process.orientation_search_radius_mm` | num | 否 | 默认 5（R17） |
| `process.fixed_search_radius_mm` | num | 否 | 固定节点导入搜索半径，默认 0.01 |
| `fixed_nodes_csv` | path | 条件 | 旧格式；`constraints.mode=fixed_nodes` 时使用，每组必须恰好命中 1 个节点 |
| `constraints` | obj | 否 | `mode=fixed_nodes` 或 `rigid_tooling`；后者的 `items[]` 参数化 bearing/clamping、geometry、unit、刚度 |
| `contact_ruleset` | str | 是 | `build/rulesets/<名>.yaml`。回归 case：当年求解所用模板，固定不改（`fillet` / `fillet_tube`）。项目 case：plan 给出的能力默认值（T 接头 `process_default`），或该能力 `contact.explicit` 列出的替代方案（如 `fillet`，焊道优先单向接触），替代方案的 provenance 必须是 `simufact_official_example` / `user_confirmed` / `process_document`，否则 CT1 阻断；不许为了算通而切换 |
| `solver.domains` / `.threads_per_domain` | int | 否 | case 级并行覆盖；未给则取 `config.yaml` |
| `gate.end_time_s` | num | 是 | 门禁写 .dat 的结束时间（与参照相同，例如 280） |
| `gate.independent_bead` | bool | 否 | 门禁带 `--independent-bead`（R20） |
| `run.end_time_s` | num | 是 | 求解结束时间（例如 20） |
| `run.temperature_limit_percent` | num | 是 | 同截面峰值温差上限 |
| `post` | obj | 否 | 有此块时 `all` 才跑 `post` 步；单模型结果提取，不需要参照算例 |
| `post.probes[].name` | str | 是 | 测点名，输出表的列名 |
| `post.probes[].body` | str | 是 | 结果体名或其中一段（按 `*_FV_*<body>*_<inc>.ARC` 匹配，匹配到多个会报错） |
| `post.probes[].point_mm` | [3] | 是 | 测点的物理坐标（模型坐标系，mm）。测点按坐标定义，不按节点号：两套网格节点号不同，节点号跨网格没有可比性 |
| `post.probes[].max_distance_mm` | num | 否 | 最近节点离该坐标的上限（默认 2.0）。超过即判定为「这套网格代表不了这个点」，该步失败；最近节点的热循环仍然输出，但标为 `[REFERENCE ONLY]`、`comparable_across_meshes=false`，不许拿它跟另一套网格在同一点的值做对比 |
| `post.stride` | int | 否 | 每隔几个结果增量导出一次（默认 1，全导）；采样越稀，峰值和 t8/5 越不准，输出里给出 `peak_missed_by_sampling_K` 和窗口内样本数 |
| `post.increments` | [int] | 否 | 显式指定要导出的结果增量号，给了就忽略 `stride` |
| `post.final_values` | [str] | 否 | 末增量在测点处提取的结果量（默认 `PKTEMP,TOTDISP,EFFPLS,EFFSTS`；可用名见 `docs/simufact-api-notes.md`，求解器单位制 m/Pa/K） |
| `post.keep_csv` | bool | 否 | 保留 ArcToolCmd 导出的中间 CSV（默认删除；全量导出可能上 GB） |
| `reference` | obj | 否 | 参照工程；不给时跳过门禁对比和温度对比 |
| `reference.dat` | path | — | 参照 .dat |
| `reference.proc_dir` | path | — | 含参照 `robots_properties.xml` 的目录（`--proc-ref`） |
| `reference.contact_table_xml` | path | 否 | 仅用于接触表逐字段对照 |
| `reference.contact_name_map` | obj | 否 | 参照接触体内部名 → `torch1/torch2/web/plate` |
| `reference.bead_sections_json` | path | — | 参照焊道截面坐标（温度对比按截面归并） |
| `reference.results.<label>.arc` / `.bead_key` | path/str | — | 参照结果 ARC（同一时刻）和截面 json 中的焊道键 |

## explicit_web (optional, 00_hm)

Meshes one web face straight from the CAD geometry with explicit T2_pair/T3 transitions (`hm/explicit_web.py`,
`mesh_transition/web.py`). When the case has this block, `weldsim.py hm` tries it first.

| field | default | meaning |
|---|---|---|
| `step` | required | STEP file, relative to the case file |
| `face` | required | ADVANCED_FACE id of the web face |
| `root_geometry_ids` | `[]` | weld root edges; empty = the longest side of the outline |
| `fine_mm` / `coarse_mm` / `far_mm` | 5 / 13 / 14.5 | weld-side size, size after the transition row (ratio 2..3), far-field height target |
| `buffer_rows` / `coarse_rows` | 2 / 2 | fine rows before, coarse rows after the transition row |
| `layers` | 2 | elements through the thickness |
| `thickness_mm` | null | null = distance to the parallel face |
| `fallback` | `automesh` | geometry unsupported: `automesh` runs the `base_mesh` backend, `fail` stops |
| `plate_bdf` | null | plate mesh copied to `00_hm/base_plate.bdf` when `plate` is not given; null = `base.plate_bdf` is used by prep |
| `plate` | null | mesh the plate too: `face` (profile face at one end of a plate swept along the web's thickness axis), `backend` (`auto`: `explicit_plate_sweep` when both end profiles agree within 0.05 mm, else `mapped_plate`; or one of the two, forced), `along_mm` 5, `layers` 3, `fine_mm` 5, `far_mm` 16, `growth` 1.3, `weld_leg_mm` 6, `fine_margin_mm` 10, `deck`. The fine zone across the weld is the web thickness plus leg plus margin on each side, taken from the web geometry; the web faces are always node levels. `mapped_plate` (`mesh_transition/mapped_plate.py`): end profiles of one topology, stations matched per edge, every level's boundary projected onto its CAD face; a hard point moves the nearest node of its level (at most half a cell) |
| `constraints` | `[]` | `[{"name", "body": "web"\|"plate", "dirs": "xyz", "point_mm": [x, y, z]}]`: support points as positions, not node ids. Each is made an exact node of its body (hard point), written to `00_hm/fixed_nodes.csv` (`name;dirs;target;x;y;z`, the build step format) and checked to lie within 0.01 mm of a node |
| `deck` | comp 1, PID 0, ids from 1 | component id/name/colour, PID and id bases of `base_web_hm.bdf` |
| `acceptance` | SJ 0.3, 11 mm, run 3, 0.05 mm | min scaled Jacobian; outward cells below `outer_min_cell_mm` may not form a run longer than `max_outer_flagged_run` columns; boundary nodes within `max_boundary_deviation_mm` of the CAD edges |

Supported: one planar face in a coordinate plane (any of the three; the in-plane axis order follows the root), one
weld root (tangent breaks < 90 deg inside it), plate ends next to it, a far boundary that may be cut by notches. Unsupported and refused (never attempted): non-planar faces,
oblique planes, outlines that do not split into root / ends / far boundary. An unsupported geometry takes the
fallback; a supported geometry that fails a check fails the step (no silent switch of mesher).

Outputs: `base_web.bdf` (R1: GRID*, CHEXA, ids from 1, the file prep reads), `base_web_hm.bdf` (HyperMesh deck with
the `deck` organisation, for inspection); with `plate`, `base_plate.bdf` and `base_plate_hm.bdf`; with `constraints`,
`fixed_nodes.csv` and `constraints_resolved.json`; `manifest.json` (`mesher`, geometry recognition, every check).

Constraints as positions. A support written as a node id belongs to one mesh; after a remesh "the nearest node"
drifts by up to half a cell. A constraint point here is honoured by the mesher: through the thickness or extrusion
it must be (web) or is made (plate) a node level; in the face the nearest node is moved onto it, a boundary node
only along its own CAD edge. A point that cannot be honoured without spoiling an element stops the step. A point
meant to lie on an edge should be given on that edge: a legacy node position lies on a chord and can be off the CAD
curve by a few hundredths of a millimetre, so project it once when converting old constraints.


## role, geometry, provenance, solver (all cases; 2026-09)

| field | meaning |
|---|---|
| `role` | `project` (a real part; default) or `regression` (reproduces a finished study; never a template). `weldsim.py new` always writes `project` |
| `geometry.step` | the part's STEP file; required for a project case. `inspect` / `plan` read it |
| `provenance.<key>` | `{source, ref, note, approval}` for every key of `common/provenance.py:CRITICAL`. `source` is one of `cad_measured`, `simufact_official_example`, `user_confirmed`, `process_document`, `derived`, `estimated`, `borrowed_case`, `machine_probe`, `benchmark_measured`. `borrowed_case` needs `approval: {by, date, scope}` |
| `solver` | `{domains, threads_per_domain}`; source must be `machine_probe`, `benchmark_measured` or `user_confirmed` |

Preflight rules: PV1 every critical parameter established and sourced (a project case may not leave one out), PV2
borrowed only with approval, PV3 estimated listed, PV4 no input file byte-identical to a regression case input unless
declared and approved, CAP1 a CAD-based case needs a capability plan without gaps or unverified backends, CT1 the contact rule set is the plan default, the regression baseline, or an explicit option with an official / user / process-document source, LC1 a formal run needs inspect,
plan, provenance and review on the current case file. Outputs of the lifecycle stages: `<out>/inspect/features.json`,
`<out>/plan/plan.json`, `<out>/provenance/manifest.json`, `<out>/machine/machine.json`, `<out>/dashboard/index.html`,
`<out>/lifecycle.json`.

## Filled from the plan (project cases, 00_hm)

For a project case `weldsim.py hm` completes the `explicit_web` block from `plan/plan.json`: `step` (from
`geometry.step`), `face` and `root_geometry_ids` (the web face and its root edges), `plate.face` and `plate.backend`
(the plate profile face; `explicit_plate_sweep` or `mapped_plate` from the plate's section along the web normal).
Anything the case gives itself is kept; sizes left out take the mesher's algorithm defaults. A plan whose base part has
no backend stops `hm`. The manifest lists what came from the plan (`from_plan`); the mesh checks are also recorded as
the lifecycle stage `mesh_gate`.

Plan statuses: `supported` (whole chain verified: CI tests and solver runs named in `capability/matrix.yaml`),
`partial` (verified, needs a user-supplied input such as a mesh), `unverified` (a backend exists but the chain has not
been built and smoke-run with it: meshing and prep work, a formal run is blocked by CAP1), `gap` (detected, no
capability). Every joint carries `detected` and `supported`.
