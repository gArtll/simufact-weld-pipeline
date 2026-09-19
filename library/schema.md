# 焊接仿真参数库 schema v0

日期：2026-09-14。一行 = 一个"工况 × 网格"组合。
**单元格写法**：`值（来源文件:行号 或 XML 节点路径）`。多来源用 `；` 分隔。找不到写 `未找到`，不估算。
**单位**：长度 mm，时间 s，速度 mm/s，功率 W。原始文件用 m 或 m/s 的，一律换算后写入，并在来源里注明"换算自 m"。

| # | 字段名（CSV 列名） | 类型 / 取值 | 说明 |
|---|---|---|---|
| 1 | case_id | str | 工况 ID，格式 `<模型>-<网格>`，如 `<model>-mesh2` |
| 2 | model | str | 模型名（如 tube_on_plate） |
| 3 | mesh_id | str | 网格编号或名称 |
| 4 | joint_type | 枚举：T形 / 平板对接 / 环焊 / 双侧角焊 | 可多值，用 `+` 连接 |
| 5 | welding_process | str | 焊接方法（FCAW / GMAW …） |
| 6 | base_material | str | 母材牌号 |
| 7 | thickness_mm | str | 各板厚，`部件=值` 形式 |
| 8 | filler_wire | str | 焊丝牌号 |
| 9 | leg_length_mm | float | 焊脚 |
| 10 | heat_source_model | str | 热源模型名 |
| 11 | hs_af_mm | float | 双椭球前半轴 |
| 12 | hs_ar_mm | float | 双椭球后半轴 |
| 13 | hs_b_mm | float | 半宽 |
| 14 | hs_d_mm | float | 深度 |
| 15 | hs_gauss_M | float | 高斯参数 |
| 16 | hs_power | str | `I×U=…W` 或 `Q=…W` |
| 17 | hs_efficiency | float | 热效率 η |
| 18 | hs_ff_fr | str | 前后能量分配 f_f/f_r |
| 19 | weld_speed_mm_s | float | 焊速 |
| 20 | weld_length_mm | float | 单条焊缝长度（热源路径） |
| 21 | size_weld_zone_mm | str | 焊缝区单元尺寸 |
| 22 | size_transition_mm | str | 过渡区尺寸 |
| 23 | size_far_field_mm | str | 远场尺寸 |
| 24 | layers_through_thickness | str | 板厚方向层数，`部件=层数` |
| 25 | element_type | str | 单元类型与数量 |
| 26 | elements_base | int | 母材单元数 |
| 27 | elements_bead | int | 焊道单元数 |
| 28 | elements_total | int | 总单元数 |
| 29 | bead_spec | str | 焊道截面、质量等级、细化等级 |
| 30 | weld_step_rule | str | 焊接段步长规则 |
| 31 | cooling_max_step_s | str | 冷却段步长上限（写实际进入 .dat 的值） |
| 32 | end_time_s | float | 分析结束时间 |
| 33 | constraint | str | 约束方式 |
| 34 | parallel | str | 域数 × 线程数 |
| 35 | solver_exit | str | 求解退出码 |
| 36 | software_version | str | Simufact / MARC 版本 |
| 37 | validation_basis | str | 校核依据：与实测或文献比对了什么量、偏差多少；没有就写"无" |
| 38 | source_files | str | 主要来源文件路径 |
| 39 | entry_date | date | 录入日期 |
| 40 | confidence | 枚举：已校核 / 文献 / 估算 | 按下方规则判定 |
| 41 | notes | str | 与计划不一致处、未解决的矛盾 |

## 可信度判定规则
- **已校核**：数值与实测或文献结果比对过，且给出偏差。
- **文献**：参数直接取自文献或厂商资料，但没有做结果比对。
- **估算**：经验关系估算或仅为计划值。

一行包含多类数值时，**取最低一级**。
一行的数值可能有两种来源：一种是"求解已跑通（exit 3004）"，另一种只是计划文档里写的。两种可以并存，但必须在 notes 里说明。
