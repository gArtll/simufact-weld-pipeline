# 参数库 v1

`参数库_v1.csv` 是检索数据，一行代表一个“工况 × 网格”。公开仓库里的这个文件**只有表头**：真实研究的行（包括作者
自己的算例）不进公开仓库，放在使用者自己的私有参数库里。单元格采用“值（来源）”，检索器只解析值。

检索打分只用 `joint_type`、`welding_process`、`base_material`、`thickness_mm`、`confidence`、`solver_exit`。
检索命中的数值只是线索：新 case 的每个关键参数仍要按 `AGENTS.md` 写 provenance，借用回归 case 的值默认 BLOCK。

## 列

| 列 | 含义 |
|---|---|
| `case_id` | 工况唯一 ID |
| `model` | 模型族名称 |
| `mesh_id` | 网格编号、组件组合与说明 |
| `joint_type` | 接头类型；可组合 T 形、环焊、双侧角焊等 |
| `welding_process` | 焊接方法，如 FCAW |
| `base_material` | 母材牌号 |
| `thickness_mm` | 各部件板厚，mm |
| `filler_wire` | 焊丝牌号 |
| `leg_length_mm` | 焊脚或截面边长，mm；以单元格溯源定义为准 |
| `heat_source_model` | 热源模型 |
| `hs_af_mm` | 双椭球前半轴 af，mm |
| `hs_ar_mm` | 双椭球后半轴 ar，mm |
| `hs_b_mm` | 双椭球半宽 b，mm |
| `hs_d_mm` | 双椭球深度 d，mm |
| `hs_gauss_M` | 高斯参数 M |
| `hs_power` | 电流、电压与功率 |
| `hs_efficiency` | 热效率 |
| `hs_ff_fr` | 前、后能量分配 |
| `weld_speed_mm_s` | 焊速，mm/s |
| `weld_length_mm` | 单条热源路径长度，mm |
| `size_weld_zone_mm` | 焊缝区网格尺寸，mm |
| `size_transition_mm` | 过渡区网格尺寸，mm |
| `size_far_field_mm` | 远场网格尺寸，mm |
| `layers_through_thickness` | 各部件厚度方向层数 |
| `element_type` | 单元类型与构成 |
| `elements_base` | 母材单元数 |
| `elements_bead` | 焊道单元数 |
| `elements_total` | 总单元数 |
| `bead_spec` | 焊道截面、质量等级、截面间距等 |
| `weld_step_rule` | 焊接阶段时间步规则 |
| `cooling_max_step_s` | 冷却阶段最大时间步，s |
| `end_time_s` | 分析结束时间，s |
| `constraint` | 固定节点、支撑与夹具方式 |
| `parallel` | 域数和每域线程数 |
| `solver_exit` | 求解器退出码；3004 为正常结束 |
| `software_version` | Simufact / Marc 版本 |
| `validation_basis` | 实测或文献校核量、偏差；没有则无 |
| `source_files` | 主要证据文件路径 |
| `entry_date` | 录入日期 |
| `confidence` | 整行最低可信度 |
| `notes` | 计划与实际差异、矛盾、限制 |
| `mesh_lineage` | 网格谱系和求解工程归属 |

## 可信度

- `已校核`：结果已与实测或文献量比对且给出偏差；检索加 20 分。
- `文献`：参数直接来自文献或厂商资料但未做结果校核。当前检索器保留该显示值，排序按“估算”中性处理。
- `估算`：经验估算、筛选设定或仅有求解通过而无实测标定；不加不扣。
- `计划`：尚未形成可用求解工况；检索扣 50 分。CSV v1 的个别行用“计划，未求解”，检索按计划处理。

正常退出码不是可信度：`solver_exit != 3004` 另扣 30 分。接头不同直接排除；方法不同扣 40；材料类别不同扣 30；板厚按对数比例连续扣分。第一名低于 90 分时明确打印“无可靠匹配，以下仅供参考”。

## 命令

```powershell
python weldsim.py suggest --joint fillet --process FCAW --material Q420 --thickness 6,10 --n 3
python weldsim.py suggest --joint ring --process FCAW --material Q420 --thickness 5,10 --emit-case out.json
```

`--emit-case` 只把第一名已有数值写入草稿。文件、工程和参照路径均为空，`_todo` 明示必须补齐；草稿不能直接送入求解。

