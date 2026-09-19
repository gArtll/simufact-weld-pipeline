# 独立复现验证

*English readers: this is the acceptance brief for an independent reproduction — a fresh clone on another machine,
another agent, a new model. The pipeline itself is described in [how-it-works.md](how-it-works.md); the rules the agent
must follow are in [../AGENTS.md](../AGENTS.md).*

这份文件是给**另一台机器上、另一个 AI Agent**的验收任务书。目的不是再跑一遍已有算例，而是回答一个问题：

> 只凭这个公开仓库和它的文档，换一个人、换一台机器、换一个模型，能不能走完 prep → gate → build → run → post？

这比原作者在自己电脑上再跑一次更能说明问题：原作者的机器上有私有参照算例、有配好的路径、有没写进文档的习惯。

## 先说清楚这个验证不能证明什么

- **不能证明仿真结果对**。本仓库没有实验标定，热源是文献/筛选值（`library/参数库_v1.csv` 置信度一栏写着"估算"）。
  复现成功只说明**流程可迁移**，不说明温度场准确。
- **不能证明新接头类型能用**。目前只支持两类：腹板立板角焊、圆筒立平板环焊。别的接头是开发工作，见
  [how-it-works.md](how-it-works.md#添加新接头类型要改什么)。
- **`gate` 步没有参照算例就没有意义**。输入核对是拿新 `.dat` 和一个"界面里搭好、算到 exit 3004"的参照 `.dat` 比五个
  关键段。复现方必须自己有一个这样的参照算例，或者退回 `gate --self`（只能证明自洽，不能证明对）。这是本方案的硬约束，
  不是可以绕过的实现细节。

## 两级验收

### 一级：不需要 Simufact（任何人都能做，半小时）

全新 clone，装 `numpy scipy pyyaml pytest`，然后：

```bash
python -m pytest tests -q
```

```bash
python gate/run_regressions.py
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

```bash
python weldsim.py gate cases/example_tube_synthetic.json --self
```

```bash
python tools/check_no_absolute_paths.py
```

```bash
python tools/check_private_data.py
```

**通过判据**：

| 命令 | 全新 clone 上应该看到 |
|---|---|
| `pytest tests -q` | 全部 passed；skipped 的是需要 Simufact、HyperMesh 或私有数据的用例（数量见最新一次 CI） |
| `gate/run_regressions.py` | 一致 0，不一致 0，跳过 17。**全部跳过是对的**：每个回归用例都要一份存好的 `.dat`，仓库里一份都没有（根目录 `.gitignore` 忽略 `*.dat`）。这一条只验证它不报"不一致" |
| 四条 `prep` / `gate --self` | 退出码 0；`01_prep/manifest.json`、`03_gate/manifest.json` 里全部 checks 为 ok |
| `tools/check_no_absolute_paths.py` | `0 absolute path(s)` |
| `tools/check_private_data.py` | `0 finding(s)` |

这一级在 GitHub Actions 上也跑（`.github/workflows/ci.yml`，Linux + Windows × Python 3.10 / 3.13）。如果你的环境结果
和上表不一样，那是本仓库的问题，请连同环境信息一起反馈。

一级不通过就先别往下走——说明文档或依赖有问题，这本身就是最有价值的复现结论，请连同报错原文一起反馈。

### 二级：新模型走完全流程（需要 Simufact Welding 2024.2 + MARC，可选 HyperMesh）

复现方需要自己准备，仓库里都没有：

| 需要 | 说明 |
|---|---|
| Simufact Welding 2024.2 + MARC 2023.4 许可证 | `build`、`gate`、`run`、`compare`、`post` 都要 |
| 材料卡 `.xmt` | 格式与来源见 [materials/README.md](../materials/README.md)；仓库不含材料卡 |
| 母材网格 bdf | 自己的几何；或用 `hm` 步现划（只支持腹板立板角焊） |
| **参照算例** | 在 Simufact 界面里搭好、求解到 exit 3004 的同类工程，用于 `gate` 和 `compare` |
| 焊缝窗口 csv、热源 xml、工艺 xml | 格式见 [case_schema.md](../case_schema.md) |

**AI Agent 的任务**（先读 [AGENTS.md](../AGENTS.md)，写入边界和交接流程照那里的来）：

| 步 | 做什么 | 通过判据 |
|---|---|---|
| 0 | 读 `AGENTS.md`、`README`、`docs/`、`case_schema.md`；确认写入边界 | 能复述第 0 步的写入边界表 |
| 1 | `config.example.yaml` → `config.yaml`，填 `runscript`、`arctool`、`hmbatch` | 一级验收全过 |
| 2 | `weldsim suggest` 查参数库，写 `case.json`，请用户审 | 参数库里没有的数值走 `AGENTS.md` 的文献入库四步，不许直接写进 `case.json` |
| 3 | `weldsim.py preflight <case.json>` | block = 0；warn 全部在报告里列出 |
| 4 | `weldsim.py prep <case.json>` | `01_prep/manifest.json` 全部 ok：R1、R2 detJ 非正 0、R8、R19、CSV 换行、10 µm 近重合 0 |
| 5 | `weldsim.py build <case.json>` | `02_build/manifest.json` 全部 ok |
| 6 | `weldsim.py gate <case.json>` | `check_short_v3` 结论"通过"或"通过（数值等价: ORIENT）"，exit 0，不带 `--allow` |
| 7 | `weldsim.py run <case.json>`（用户在场启动） | `.sts` exit 3004 且所有载荷工况 100 %；求解器用的 `.dat` 与核对过的一致；`solver_stats.txt` 里的 `Run warnings` 照实报，不判失败 |
| 8 | `weldsim.py compare <case.json>` | 同截面峰值温差 < `run.temperature_limit_percent` |
| 9 | `weldsim.py post <case.json>` | 每个测点的最近节点在 `max_distance_mm` 以内；超了就如实报"这套网格代表不了这个点" |

## 要反馈什么

按 `AGENTS.md` 的交付报告模板写一份，**尤其要写清下面这些**——它们才是这次验证的产出：

1. **文档里缺的、说不清的、写错的每一处**，原文引用 + 你当时卡在哪。这是最有价值的部分。
2. **每一步的 `manifest.json` checks 原文**，不要转述。
3. **你自己动手改了仓库里哪些文件**。按 `AGENTS.md`，代码目录是只读的；如果你不得不改才能跑通，那就是仓库的缺陷，
   要单独列出来。
4. **环境差异**：操作系统、Python 版本、Simufact 版本、HyperMesh 版本。本仓库只在 Windows 10 + Simufact 2024.2 +
   MARC 2023.4 + HyperMesh 2026 上验证过。
5. **失败也要报**。哪一步退出码非 0、原文是什么、你判断的原因。一次说清楚原因的失败，比一次"跑通了"更有用。
6. 如果求解退出 3015：对照 `tests/failure_logs/README.md` 的失败特征，以及 `AGENTS.md` 的*失效定位方法*，说明你的现象
   是不是同一类。

## 复现方需要知道的已知缺口

- 仓库里没有材料卡、参照 `.dat`、结果 `ARC`、工装 `ARC`（见根目录 `.gitignore`）；全新 clone 上 17 个回归用例**全部**
  报"跳过"，这是预期结果，不是故障。
- 本工具自己建的模型**还没有跑过一次完整焊接+冷却求解**，只跑过短加热算例。`docs/validation.md` 里那六次完整求解全是
  在 Simufact 界面里搭的模型。二级验收如果跑通了完整求解，那正是目前缺的那块证据。
- `post` 的力学量目前只在测点上取末增量的值，没有做截面或云图输出。
- 判定阈值（温差 5 %、ORIENT 2e-3、NDSQ 0.005 mm）是验证轮次里定的经验值，不是物理定律，见 `AGENTS.md`。
