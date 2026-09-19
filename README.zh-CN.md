# simufact-weld-pipeline

[![tier-1](https://github.com/gArtll/simufact-weld-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/gArtll/simufact-weld-pipeline/actions/workflows/ci.yml)
[English](README.md)

**面向 Simufact Welding 的无界面、规则驱动的前处理与校核流水线。** 从零件的 STEP 出发，识别焊接接头，判断能做什么、
用什么方法做，把零件划成结构化六面体网格，生成焊道，用脚本搭建 Simufact 工程，并在花上数小时做焊接仿真之前，
先证明求解输入（`.dat`）就是它应该是的样子。

所有随零件变化的决策都可追溯：每个工艺、工装、机器参数都带来源，不从旧 case 继承任何值；固定的审查页必须干净，
才允许启动正式长算。整条流水线按明确规则（[AGENTS.md](AGENTS.md)）交给 AI 编程助手执行。

```mermaid
flowchart LR
  A[new<br/>空白 case] --> B[inspect<br/>STEP 接头 + 坐标系]
  B --> C[plan<br/>能力矩阵]
  C --> D[provenance<br/>参数来源]
  D --> E[review<br/>审查页]
  E --> F[hm<br/>结构化 HEX]
  F --> G[mesh gate]
  G --> H[preflight]
  H --> I[prep<br/>焊道]
  I --> J[build<br/>Simufact 工程]
  J --> K[gate<br/>.dat 输入核对]
  K --> L[run]
  L --> M[post / report]
```

## 已实现的能力

| 方面 | 做什么 | 位置 |
|---|---|---|
| **STEP 接头识别** | 读 B-rep（平面、圆柱、B 样条曲面），按面的接触关系识别零件（平板、曲面板、管、实体）和接头（T 形、对接、搭接、管-板、管-管 T 接），找出焊缝，判断零件等截面还是变截面 | `capability/classify.py`、`capability/geometry.py` |
| **能力规划** | 用接头特征查能力矩阵：网格后端、焊道与接触策略、必须由用户提供的工装与工艺输入、官方示例（只借方法）。识别到只算 *detected*，整条链有 CI 测试和求解记录才算 *supported*；其余报能力缺口，绝不套用"最相近的旧 case" | `capability/planner.py`、`capability/matrix.yaml` |
| **参数来源与生命周期** | 新 case 从空白开始；每个关键参数都要有来源（CAD、官方示例、用户、工艺文件、推导、实测……）；借用其他 case 的值未经批准一律阻断，复制文件按内容识别。固定阶段 + 状态文件；审查页上有未决项时拒绝正式长算 | `common/provenance.py`、`common/lifecycle.py` |
| **接头局部坐标系** | 在 CAD 上量出腹板法向、焊缝走向、接头法向，网格和焊道都在这个坐标系里做，STEP 原样使用（测试：旋转后的 T 接头得到旋转后的焊道，误差 < 1e-6 mm） | `capability/classify.py`、`prep/frame.py` |
| **结构化 HEX 网格** | 腹板面按 CAD 轮廓划分，显式过渡模板（焊缝处细、远场粗、缺口、约束硬点），沿厚度扫掠；全六面体，带雅可比、拓扑、CAD 边界偏差检查 | `mesh_transition/web.py`、`hm/explicit_web.py` |
| **底板后端** | 等截面板扫掠，层厚远离焊缝逐渐放大；**mapped plate** 处理变截面板：两端截面逐站对应，每层投影到真实 CAD 面（平面、圆柱、B 样条） | `mesh_transition/plate.py`、`mesh_transition/mapped_plate.py` |
| **焊道** | 焊根线、等弧长角焊截面（开口焊缝与闭合环）、微米级近重合节点吸附、外表面焊缝线、detJ 检查 | `prep/` |
| **接触规划** | 没有全仓库统一默认值：新项目取能力默认值并给出依据；显式替代方案（焊道优先单向接触）只在有官方/用户/工艺文件来源时启用；回归 case 保留当年求解用的接触 | `common/contact.py` |
| **Simufact 脚本** | 用 `runscript` 搭工程，API 没开放的部分（轨迹方向设置、接触表）离线补丁 | `build/` |
| **输入核对** | 解析求解输入 `.dat`，把关键段与参照或自身比对——开发中所有静默差异都是它抓出来的 | `gate/` |
| **审查页** | 每个 case 一个静态 HTML 页，每个阶段结束后由各阶段 manifest 重新生成：接头、规划、接触决策、参数来源、机器、网格检查、求解统计 | `ui/report.py` |
| **求解统计与后处理** | 从 `.sts` / `.out` 读退出码、进度、翻转单元、回退重试的增量；按物理坐标取测点，给热循环、t8/5、冷却速率 | `run/` |
| **CI** | 单元与集成测试、两个合成算例跑 `prep` 与 `gate --self`、输入核对回归表、绝对路径与私有数据扫描；Windows / Ubuntu × Python 3.10 / 3.13 | `.github/workflows/ci.yml` |

## 支持矩阵

| 接头 | 状态 |
|---|---|
| T 接头，平面腹板（任意轴向摆放），等截面底板 | **supported** |
| T 接头，变截面底板（`mapped_plate`） | 可划网格、可 prep；用它完成 build 与短跑前为 **unverified**，正式长算被阻断 |
| 管-板，外圆闭合环角焊 | **partial**：焊道、建模、核对支持；母材网格由用户提供 |
| 对接、坡口/多层多道、搭接、管-管 T 接、斜腹板/曲腹板、两件以上装配 | **能识别，不支持**（能力缺口，附官方参考） |

## 快速开始

Python ≥ 3.10，依赖 `numpy`、`scipy`、`pyyaml`、`pytest`。以下步骤不需要 Simufact 和 HyperMesh。

```bash
pip install numpy scipy pyyaml pytest
```

```bash
python -m pytest tests -q
```

```bash
python weldsim.py prep cases/example_synthetic.json
```

```bash
python weldsim.py gate cases/example_synthetic.json --self
```

```bash
python weldsim.py prep cases/example_tube_synthetic.json
```

从 STEP 开始一个新零件（审查页在 `runs/part/dashboard/index.html`）：

```bash
python weldsim.py new part.json --step part.stp
```

```bash
python weldsim.py inspect part.json
```

```bash
python weldsim.py plan part.json
```

```bash
python weldsim.py review part.json
```

`build`、对参照的 `gate`、`run`、`post` 需要 Simufact Welding 许可；本机路径写在 `config.yaml`（由
`config.example.yaml` 复制）。字段说明：[case_schema.md](case_schema.md)；各步原理：[docs/how-it-works.md](docs/how-it-works.md)；
独立复现任务书：[docs/reproduce.md](docs/reproduce.md)。

## 验证

工具在真实焊接模型（曲面板上的 T 接头、管-板环焊）上开发，对照 Simufact 界面搭建的参照工程：无界面工程逐段复现了参照
求解输入，工具生成的焊道在短跑中与参照峰值温度差在 5 % 以内，一个反复出现的求解失败被定位到接触近重合并修复。这些模型
不公开；它们确立的结论以方法形式写在 [docs/validation.md](docs/validation.md)，CI 每次推送都在合成输入上重新检查流水线。
热源为文献/筛查值，未做实验标定。

## Simufact 脚本要点

详细：[docs/simufact-api-notes.md](docs/simufact-api-notes.md)。

| 规则 | 内容 |
|---|---|
| R6′ | `calculate_all_orientations`：仅新建工程，每条轨迹一次；`calculate_all_projections`、`Trajectory.delete_geometry`、`Project.delete_geometry`、`split_at`：禁用 |
| R7 | 轨迹设置与接触表：关闭 Simufact，离线 XML（`robots_properties.xml`、`_contact_table/properties.xml`） |
| R17 | 外表面焊缝线：`calculate_all_orientations` 的 `search_radius=5 mm`；默认半径 → `ACCESS VIOLATION` |
| R18 | 焊缝线 CSV 表头 `0;1;0;0` → global-vector；导入后 `wl.orientation = "local-vector"` |

| 禁用方法 | 后果 |
|---|---|
| `calculate_all_projections` | 焊缝线移到表面；`connect_to_nodes`、方向模式、变化角重置；NDSQ 与方向表消失 |
| `Trajectory.delete_geometry` | 已挂载焊道上 `ValueError` |
| `Project.delete_geometry` | 下一次 `import_geometry` → `ACCESS VIOLATION` |
| `Trajectory.split_at` | 焊道分配与方向设置清空 |

## 路线图

| 下一步 | 内容 |
|---|---|
| 求解资源 | 实测而非假定：对 domain × thread 组合做短跑基准（墙钟、CPU/墙钟、内存、许可证令牌）→ 实测的并行设置；长算前给出运行时间预估 |
| 工艺稳健性 | 时间步与焊道截面间距联动提示（R21）、夹持 fixation 起止时间透传、2 s 腹板立板短跑算例（同时端到端验证 `mapped_plate`） |
| 新接头类型 | 对接、搭接、多层多道坡口、曲线与多条焊缝、装配——每种都带合成测试和对应官方示例 |
| 管件 | 管-板母材网格的 CAD 划分器 |

设计与分阶段计划：[docs/architecture_refactor.md](docs/architecture_refactor.md)。

## 数据与许可

代码为 MIT 许可；MIT 不自动覆盖数据。仓库里只有合成数据（`cases/example_synthetic`、`cases/example_tube_synthetic`、
`tests/failure_logs`），每个文件都由仓库里的脚本生成，并在 `tools/public_data_allowlist.yaml` 登记来源。不包含 Simufact
官方示例的文件、几何或数值；`official/references.yaml` 只写本机安装目录下的示例路径和所示方法。真实模型的数据不提交，
CI 会拦截（`tools/check_private_data.py`）。

## License

[MIT](LICENSE) · [CITATION.cff](CITATION.cff)
