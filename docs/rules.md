# Rules

Generated from `common/rules.yaml` by `tools/gen_rules_doc.py`. Do not edit by hand.

| Field | Values |
|---|---|
| stage | prep / build / gate / run / none |
| severity | block / warn |
| enforced by | `file:function` / manual |
| source | 内部算例 (an unpublished development model) / Simufact 硬规则 / Tcl 硬规则 / E2 任务书 / F1 |

## Simulation rules (32)

| ID | Rule | Stage | Severity | Enforced by | Source |
|---|---|---|---|---|---|
| R1 | Simufact 导入 bdf 只认大字段 GRID* + 固定字段 CHEXA | prep | block | `common/bdf.py:check_r1` | 内部算例 |
| R2 | 两侧焊道按焊侧判断绕序，2x2x2 高斯点 detJ 非正为 0 | prep | block | `gate/check_detj.py:detj` | 内部算例 |
| R3 | 焊道与母材不要求共点，距离分布与界面焊道同型且 detJ 全正 | gate | block | `gate/check_bead_nodes.py:classify` | 内部算例 |
| R4 | 节点连接来自 XML connect_to_nodes=true，写 .dat 时生成 | build | block | `build/patch_trajectory.py:patch` | 内部算例 |
| R5 | open_project 重存删掉 nodal_connection_result 两字段属正常 | none | warn | manual | 内部算例 |
| R6 | 禁用 calculate_all_projections / 重复 calculate_all_orientations / delete_geometry / split_at | build | block | manual | 内部算例（R6'） |
| R7 | 换焊道只 import_geometry，关 Simufact 后离线改 robots_properties.xml | build | block | `build/patch_trajectory.py:patch` | 内部算例 |
| R8 | 焊缝线点必须落在焊接面上 | prep | block | `prep/wl_v2.py:main` | 内部算例 |
| R9 | 求解前移走复制来的 _Results_、_watch_tmp | run | block | `weldsim.py:copy_project` | 内部算例 |
| R10 | 固定时间步覆盖手动步长告警可忽略 | none | warn | manual | 内部算例 |
| R11 | .dat 核对必须显式指定参照文件，块不同即失败 | gate | block | `weldsim.py:gate_dat` | 内部算例 |
| R12 | 热源实体名、热源文件名与 XML 内电流电压数值一致 | build | block | `common/preflight.py:check_heat_source` | E2 任务书 |
| R13 | 焊缝线、焊道、母材同源、同一次 prep 运行 | build | block | `weldsim.py:step_build` | 内部算例 |
| R14 | 导入外表面线（01_prep/wl_outer_*.csv） | build | block | `weldsim.py:step_build` | 内部算例 |
| R15 | 正式输入核对无允许差异；短跑 --allow AUTO_STEP | run | block | `weldsim.py:step_run` | 内部算例 |
| R16 | 热源实体名等于 case heat_source.entity_name | build | block | `common/preflight.py:check_heat_source` | 内部算例 |
| R17 | 外表面线 calculate_all_orientations 给 search_radius=5 mm | build | warn | `common/preflight.py:check_search_radius` | 内部算例 |
| R18 | 焊缝线 CSV 表头 0;1;0;0 导入后设 local-vector | build | block | manual | 内部算例 |
| R19 | 窗口由定义点投影确定，长度差 < 0.01 mm | prep | block | `prep/bead_outer_line.py:main` | 内部算例 |
| R20 | 独立焊道输入核对 + 同截面峰值温差 < 5% | gate | block | `weldsim.py:step_compare` | 内部算例 |
| R21 | 截面等距、间距为参数；热源单步前进 <= 截面间距 x 0.5 | prep | block | `common/preflight.py:check_advance` | 内部算例；前进量上限为 E2 任务书 |
| R22 | 接触表由接触模板生成，user_defined=true，不复制参照算例 | build | block | `build/contact_table_gen.py:generate` | 内部算例 |
| R23 | ORIENT 容差内一致（弧长 1e-5 m、方向分量 2e-3） | gate | block | `weldsim.py:gate_dat` | 内部算例 |
| R24 | 节点吸附：焊道接触面节点离母材节点 < 10 µm 时精确重合；近重合区间 10 µm–0.1 mm 计数进输入核对报告 | prep | block | `prep/weld_prep.py:main` | 内部算例 |
| PV0 | regression case: reproduces a finished study, never a template for a new case | preflight | warn | `common/provenance.py:check` | new-model review 2026-09 |
| PV1 | every critical part / process / machine parameter in use has a provenance source | preflight | block | `common/provenance.py:check` | new-model review 2026-09 |
| PV2 | borrowed_case parameters need the user's explicit approval (and stay listed) | preflight | block | `common/provenance.py:check` | new-model review 2026-09 |
| PV3 | estimated parameters are listed for review | preflight | warn | `common/provenance.py:check` | new-model review 2026-09 |
| PV4 | no input file may be a silent copy of a regression case input | preflight | block | `common/provenance.py:check` | new-model review 2026-09 |
| CAP1 | a CAD-based case needs a capability plan without gaps | preflight | block | `common/preflight.py:check_capability` | new-model review 2026-09 |
| CT1 | contact is the capability plan default, the regression baseline, or an explicit option with an official / user / process-document source | preflight | block | `common/contact.py:check` | new-model review 2026-09 |
| LC1 | a formal run needs inspect, plan, provenance and review done on the current case file | run | block | `common/lifecycle.py:run_blockers` | new-model review 2026-09 |

## Simufact hard rules (10)

| ID | Rule | Stage | Severity | Enforced by | Source |
|---|---|---|---|---|---|
| S1 | 只对可丢弃副本跑脚本；目标工程不得已存在 | build | block | `common/preflight.py:check_output` | Simufact 硬规则 1 |
| S2 | 存在 _Run_ 时工艺只读；工程不得被占用（project.lock） | build | block | `common/preflight.py:check_output` | Simufact 硬规则 2 |
| S3 | 脚本运行器不认中文路径（preflight 同时拒绝空格） | build | block | `common/preflight.py:check_paths` | Simufact 硬规则 3 |
| S4 | 不调用 Trajectory.split_at | build | block | manual | Simufact 硬规则 4 |
| S5 | 副本里不在界面重新生成焊道、不开轨迹方向对话框 | none | warn | manual | Simufact 硬规则 5 |
| S6 | 成败看 .sts/.out 退出码（3004 正常） | run | block | `weldsim.py:read_sts_exit` | Simufact 硬规则 6 |
| S7 | 冷却段最大步长非自动且有上限（<= 25 s） | build | block | `common/preflight.py:check_process` | Simufact 硬规则 7 |
| S8 | CSV 末行要有换行符 | prep | block | `common/preflight.py:check_csv` | Simufact 硬规则 8 |
| S9 | 单位 mm：import_geometry(unit=mm)、import_points(length_unit=mm) | build | block | manual | Simufact 硬规则 9 |
| S10 | 每次改动后必须跑 .dat 核对 | gate | block | `weldsim.py:gate_dat` | Simufact 硬规则 10 |

## HyperMesh Tcl hard rules (4)

| ID | Rule | Stage | Severity | Enforced by | Source |
|---|---|---|---|---|---|
| TCL1 | 先划细区域再划粗区域 | prep | block | `hm/tcl_gen.py:check_tcl1` | Tcl 硬规则 1 |
| TCL2 | mark 只能用 1 和 2 | prep | block | `hm/tcl_gen.py:check_tcl2` | Tcl 硬规则 2 |
| TCL3 | *readfile / *writefile 前加 hm_answernext yes | prep | block | `hm/tcl_gen.py:check_tcl3` | Tcl 硬规则 3 |
| TCL4 | *solid_split_by_tool 只用 tool_type=plane | prep | block | `hm/tcl_gen.py:check_tcl4` | Tcl 硬规则 4 |

## Preflight consistency checks (5)

| ID | Rule | Stage | Severity | Enforced by | Source |
|---|---|---|---|---|---|
| PF1 | case.json 列出的输入文件全部存在 | build | block | `common/preflight.py:check_inputs_exist` | E2 任务书 |
| PF2 | 材料 xmt、热源 xml、工艺 xml 可解析且根元素正确 | build | block | `common/preflight.py:check_parse` | E2 任务书 |
| PF3 | 焊接段为固定步长（time_steps_method=0，步长 > 0） | build | block | `common/preflight.py:check_process` | E2 任务书 |
| PF4 | 母材板厚方向单元层数达到 case 阈值（默认 >= 3） | prep | warn | `common/preflight.py:check_base_mesh` | E2 任务书；F1 改为 warn |
| PF5 | 热源 XML 焊速等于 process.weld_speed_mm_s | build | warn | `common/preflight.py:check_speed_consistency` | E2 任务书 |
