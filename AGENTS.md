# AGENTS.md — 给 AI 助手的工作说明

本文件写给协助使用 simufact-weld-pipeline 的 AI 助手（`CLAUDE.md` 只引用本文件）。README 只列名词；这里写原因、边界和做法。开始任何操作前先读完本文件，再读 `README.md`、`case_schema.md`、`docs/`。

## 总原则

**数值决策全由规则和参数库承担。** 网格尺寸、截面间距、热源参数（af/ar/b/d/M、电流电压、效率）、步长、焊速、约束方式，一律来自 `common/rules.yaml`、`library/参数库_v1.csv`（`weldsim suggest`）或用户给的文件。AI 不许现场编造或"经验估一个"。参数库没命中（`suggest` 打印"无可靠匹配"或候选为 0）就明说没命中，列出缺什么，让用户提供。

### 文献值入库：离线建库、在线检索

AI 只做抽取和排序，不做决定。流程固定四步：

1. AI 查到文献值，列出 DOI、页码、原文数值（原单位）。
2. 用户确认。
3. 录入 `library/参数库_v1.csv`：可信度标"文献"，来源列写 DOI。
4. 之后 `weldsim suggest` 才能命中，才能用。

**不经过参数库的数值一律不许进 `case.json`。**

## 新模型：从空白开始，不继承任何已有 case（2026-09 起强制）

起因：另一台机器上的 AI 拿到一个新模型后，默认把仓库里两个案例的夹持、支撑、约束、热源、材料、焊材、焊速、时间步、
冷却时间、并行 domains/threads 全部照搬，直到用户指出才重做。这以后由架构禁止，不靠自觉。

1. **新 case 只用 `python weldsim.py new <case.json> --step <零件.stp>` 创建。** 它从 `common/case_template.json`
   空白模板生成，所有与零件、工艺、机器有关的参数都是 null。不许复制 `cases/` 里任何 json 当起点。
2. **`cases/` 里的 case 全是 `"role": "regression"`**：它们复现已完成的研究，只用于回归，永远不是新任务的模板。
   内部模型编号同理，不许出现在主逻辑分支里（`tests/test_architecture.py` 会扫），也不许进公开仓库（CI 私有数据扫描）。
3. **每个关键参数都要写 `provenance`**，来源只能是：`cad_measured` / `simufact_official_example` / `user_confirmed` /
   `process_document` / `derived` / `estimated` / `borrowed_case` / `machine_probe` / `benchmark_measured`。
   关键参数清单在 `common/provenance.py:CRITICAL`（材料、焊材、热源、焊速、工艺 xml、焊脚、截面间距、约束/工装、接触、
   温度与换热、重力、结束时间、求解器并行）。
4. **借用别的 case 的值（`borrowed_case`）默认 BLOCK**，只有用户明确批准（`approval: {by, date, scope}`）才放行，
   且仍然在网页和 preflight 里列出。**内容相同的文件照样抓**：输入文件与任何回归 case 的输入逐字节相同，不论标成什么来源，
   都按借用处理（PV4）。
5. **并行参数没有仓库默认值。** domains/threads 只能来自 `machine_probe`（本机物理核、内存、许可证令牌）、
   `benchmark_measured`（本机短跑实测）或用户确认。
6. **能做什么由几何决定，不由 case 名决定。** `inspect` 从 STEP 识别零件和接头，`plan` 查 `capability/matrix.yaml`
   得到网格后端、焊道策略、接触策略、必须由用户提供的工装和工艺输入。不支持的组合报 capability gap 并停下，
   **不许为了跑通套用一个"相近"的已有 case。**
7. **官方示例只借"方法"。** `official/references.yaml` 把每种接头对到 Simufact 官方示例/文档，记录沿用了什么方法
   （工装种类、释放时机、接触用工艺默认……），不许把官方示例的尺寸、夹具位置、热源数值复制给新零件。
8. **网页是固定阶段。** 生命周期固定为
   `new → inspect → plan → provenance → review(网页) → hm → mesh gate → preflight → prep → build → gate → run → post → report`，
   每个阶段写 `<out>/lifecycle.json` 并自动刷新 `<out>/dashboard/index.html`。`run` 之前一定重新生成网页；
   存在未确认/借用参数、能力缺口或网页之后 case 被改过，CLI 和网页同时拒绝正式长算（LC1）。
   交付报告里给出网页路径。`hm` 的网格验收单独记为 `mesh_gate` 阶段。Streamlit（`ui/app.py`）只是增强查看器，流程不依赖它。
9. **识别到 ≠ 支持。** plan 里每个接头都有 `detected` 和 `supported`。`capability/matrix.yaml` 里一个能力只有在列出
   整条链（mesh → prep → build → smoke/run）的 CI 测试和求解记录后才能标 `supported`（`tests/test_contact_frame.py`
   检查证据存在）。对接、搭接能识别，但仍是 gap；新后端（如 `mapped_plate`）在用它完成 build 和短跑之前是
   `unverified`，可以划网格、prep、build，正式长算被 CAP1 拦住。不许因为分类器认得就在网页或报告里写"支持"。
10. **接头局部坐标系，不旋转 STEP。** `inspect` 给每个接头量出 `transverse`（腹板法向）、`tangent`（焊缝走向）、
    `normal`（底板指向腹板）；`hm`（腹板面内轴序跟随焊根）和 `prep`（`bead.frame` 或 plan 的坐标系）都在这个坐标系里做。
    不要把 STEP 刚体变换到"仓库坐标"；只有无 STEP 的回归 case 用旧坐标系 +X/+Z。
11. **底板网格后端由截面决定。** 沿腹板法向等截面 → `explicit_plate_sweep`；变截面但两端拓扑相同 → `mapped_plate`
    （逐层投影到 CAD 面）；否则 BLOCK。不要再用"hm → 外部改外形脚本 → prep"的补丁流程。
12. **接触没有全仓库统一默认值。** 回归 case 固定用当年求解的模板（不许为统一默认值去改）。项目 case 取 plan 中该能力的
    `contact.default`（T 接头为 `process_default`，依据：官方脚本建模示例无自定义接触表；另一模型上自定义
    glue-on-peak-temperature 模板首增量失效），依据写在 plan 和网页上。`contact.explicit` 里的替代方案（例如焊道优先的
    单向接触 `fillet`）只有 provenance 为官方示例/用户确认/工艺文件时才放行（CT1）；**不许为了"跑通"自动切换接触**。

## 公开仓库数据治理（2026-09 起强制）

本仓库是公开仓库。只允许：代码、文档、合成数据、以及 `tools/public_data_allowlist.yaml` 登记过的公开算例数据。

1. **未公开模型的任何数据都不进仓库**：几何、网格、工艺/热源参数、求解日志、结果数字、网格谱系、工程目录名、内部模型编号
   和项目代号。提交说明、测试说明、注释里也不许出现。需要它们的测试从 `WELDSIM_PRIVATE_CASE` 指向的私有 case 读取
   （期望值放在私有 case 的 `test_expect` 块里），没有就 skip。
2. **数据文件默认被 `.gitignore` 忽略**（STEP/BDF/STS/OUT/LOG 等，以及 `cases/` 下的 XML/CSV/JSON）；公开样本在
   `.gitignore` 里单独放行，并且必须登记在 allowlist，写明 category（synthetic / code_data）和来源。真实模型的数据（包括作者自己的算例）在公开权确认前不进仓库。
3. **失效日志样本用合成的**（`tests/failure_logs/make_synthetic.py`），不用真实项目日志。
4. CI 跑 `tools/check_private_data.py --commits <推送范围>`：扫当前树和本次推送的提交说明，拦截凭据、内部编号、
   给数值加的私有标记、私有工作目录名、未登记的数据文件。未公开模型的具体名称不写进仓库：放在仓库外的 denylist 文件
   （`WELDSIM_PRIVATE_DENYLIST=<文件>`）和 Actions secret `PRIVATE_DENYLIST` 里。本地提交前也跑一次。
5. Simufact 官方示例只引用路径和方法（`official/references.yaml`），不复制其文件、几何或数值。

## 第 0 步：写入边界

| 位置 | 权限 |
|---|---|
| `runs/`、AI 自己新建的输出目录（名字写进计划） | 可写 |
| 仓库代码（`weldsim.py`、`prep/`、`build/`、`gate/`、`run/`、`common/`、`library/`、`ui/`、`tests/`、`tools/`） | 只读，除非用户明确要求改代码 |
| 参照算例目录（`cases/*/reference/`、用户给的参照工程、任何原始 Simufact 工程） | 只读，永远 |
| 输入文件（`cases/*/inputs/`、材料卡、用户给的 bdf/csv/xml） | 只读；需要变体时复制到输出目录再改 |
| 判据（`gate/check_short_v3.py`、`common/rules.yaml` 的 severity、回归期望值） | 改之前和改之后都必须跑 `python gate/run_regressions.py`，退出码 0 才算改完 |

## 交接流程

| 步 | 用户提供 | AI 做什么 |
|---|---|---|
| 0 | — | 确认上表写入边界；只在 `runs/` 和自己的输出目录写文件 |
| 1 | 仓库 + 本文件 | 读 README、`docs/`、`case_schema.md` |
| 2 | `config.yaml`、材料卡位置 | 跑 `python -m pytest tests -q`、合成算例 `prep` 与 `gate --self`，确认环境 |
| 3 | 新零件 STEP；该零件的工艺文件（WPS/图纸）、工装方案 | `weldsim new` 建空白 case → `inspect` → `plan` → 逐项填参数并写 provenance（`suggest` 只作检索线索，命中的旧 case 数值不许直接进新 case）→ `provenance` → `review`，把网页给用户审 |
| 4 | — | `preflight` → `prep` → `build`，每步贴 `manifest.json` 的 checks 原文 |
| 5 | 参照算例（如有） | `gate`，贴输入核对原文 |
| 6 | 用户在场启动求解 | `run` 读 `.sts` 退出码，`compare` 算温差，写交付报告 |

如果你是被请来做**独立复现验证**的（全新 clone、另一台机器、自己的新模型），任务书和验收判据在 `docs/reproduce.md`，
按那份写反馈。

## 工作方式

1. 每一步先说要做什么、会写哪些文件，用户确认后再执行。
2. 结论必须能追溯到具体文件和数据；输入核对、回归、求解结果必须贴原文，不转述。
3. 判定成败只看求解器日志（`.sts` / `.out`）和物理量，不信任包装层返回的布尔值。
4. 拿不准的 Simufact API 先查安装目录 `doc/Scripting/generated/` 下的类文档，不要猜。
5. 不做界面点击自动化。

## 教训：求解没结束不许报"做完"

求解（`run` / `all`）要几分钟到几十分钟。进程还在跑、或只看到 `launch_status.json` / `analysis.wait()` 返回成功时，不许说"完成""通过"。交付报告等 `python weldsim.py all <case.json>` 的退出码出来、`04_run/manifest.json` 里 `.sts` 退出码核对完再写。中途只能报"在跑，已到哪一步"。

## 失效定位方法（求解失败、反复失败时）

来自一个未公开 T 接头模型反复退出码 3015 的定位复盘：前面多轮长算改的都是步长、热输入、约束、ALE 等全局参数，没有碰到根因；根因是焊道根部节点与腹板底角节点微米级近重合，加上默认双向接触检测造成的交叉粘合，冷却阶段局部压溃。焊道↔组件改单向接触（焊道为第一体）后全模型正常结束。工具里对应的是 R22 接触模板、R24 节点吸附和 BEAD_DIST。

1. **先比对已知失败特征。** 3015 的典型日志形态有合成样本在 `tests/failure_logs/`，README 里列了共同特征（时间步切到
   下限、翻转单元成组相邻、接触/分离类 warning 刷屏）。先看自己的现象是不是同一类，再动手。注意那几条是**优先排查方向**，
   不是已证实的根因——日志里的 warning 计数没有对应到具体接触对和增量之前，不许当成结论写进报告。
2. **报错不是失效起点。** inside out 报错比单元真正翻转晚几十个增量。不要只看报错那一步，逐增量从结果文件导出失效区的单元雅可比、塑性应变、接触应力、温度的时间线，找最早开始偏离同龄正常段的时刻。
3. **"不管改什么都死在同一处"= 原因在所有轮次共有的东西里。** 先列出各轮改过的量把它们排除，剩下的共有因素（轨迹、焊道网格、接触设置、几何）才是嫌疑。不要继续调已被排除的参数。
4. **常规网格质量检查看不见接触缺陷。** 失效单元的雅可比、长宽比可能都在中位。必须做跨部件的节点距离扫描（`gate/check_bead_nodes.py`），并看接触法向和粘合到哪个体。
5. **先证明对照组有效。** 缩短模型做区分试验时，对照组必须在同一步、同一单元复现原失效，否则短模型的结论不能外推。
6. **每个变体只改一个设置。** 开算前用 `check_short_v3` 或逐行比较证明 `.dat` 只差这一个设置；开算后确认求解器用的就是核对过的 `.dat`。
7. **判定表先写。** 运行前写好"对照是否复现 × 各变体是否通过"对应的结论和下一步，不事后解释。
8. **按温度对齐比较。** 短模型与全模型冷却速度不同，比较时按同温度而不是同增量号。
9. **跑完不等于解决。** 大步长跑完可能只是把局部化抹平（假阳性）；纯热短跑正常结束不代表热力耦合通过。短模型通过后必须用全模型确认，并监控其他近重合点。
10. **算通后查副作用。** 修复手段本身会改变结果（见 `docs/validation.md` 单向接触一节），报告里写明偏差量，不用调换热系数去"补"。

## Simufact Welding 硬规则（含原因）

| # | 规则 | 为什么 |
|---|---|---|
| 1 | 只对可丢弃副本跑脚本；绝不用脚本打开原始工程或失败结果工程 | `open_project()` 一打开就把整个工程重存一遍，不调 save 也会；重存会改掉节点连接等字段（R5） |
| 2 | 工程里有 `_Run_` 时先把 `_Run_`、`_log`、`_particles` 移到备份目录（不删）再复制 | `_Run_` 存在时工艺只读，`check_model` / `write_program_input` / `start_analysis` 报"对象是只读的" |
| 3 | 传给脚本运行器的路径只用 ASCII，不含空格 | 运行器不认中文文件名和中文路径（preflight S3 拦截） |
| 4 | 不调用 `Trajectory.split_at` | 清掉焊道分配和方向设置 |
| 5 | 复制出的工程里不在界面重新生成焊道，不打开轨迹方向对话框 | 会闪退 |
| 6 | 成败只看 `.sts` / `.out` 的 exit number，3004 为正常 | `launch_status.json`、`analysis.wait()`、runscript 退出码都可能报成功而求解失败 |
| 7 | 冷却段最大步长单独设为非自动且有上限 | 默认自动放大到 25 s |
| 8 | 固定节点 CSV、焊缝线 CSV 末行要有换行 | 项目早期踩过的导入问题（无公开交付报告）；preflight S8、prep 自检拦截 |
| 9 | 网格和 CSV 单位 mm：`import_geometry(unit="mm")`、`import_points(length_unit="mm")`；工装 surface ARC 按 m 导入 | 工装 ARC 误按 mm 导入：夹具 1000 次接近仍不接触，Marc Exit 40 |
| 10 | 每次改动后跑 `.dat` 输入核对，证明只改了想改的地方 | 包装层不报错不代表求解输入没变；轨迹块缺失、接触表、窗口长度等差异都是在 `.dat` 五个关键段里查出来的（交付报告_A3纯脚本建模_进度.md；交付报告_A4纯脚本闭环.md；交付报告_C4_weld_prep.md） |

## 禁用与受限 API（为什么 + 证据）

证据列为验证轮次的交付报告文件名（报告本身未公开，模型代号已替换）。

| API | 规则 | 为什么 | 证据 |
|---|---|---|---|
| `Robot/Trajectory.calculate_all_projections()` | 禁用（R6′） | 焊缝线点被投影到组件表面并移动；`connect_to_nodes` true→false、`calculation`→None、`orientation_mode`→Component center、变化角→0、焊缝线方向类型改回 global；`.dat` 里 NDSQ 节点序列和全部方向表消失。是破坏性操作，不是"计算节点连接"的脚本等价物 | 交付报告_任务C_calculate_all_projections.md |
| 同上，焊缝线为 global-vector 时 | 禁用 | 直接报 `RuntimeError: 方向计算不适合全局方向`（Robot 级和 Trajectory 级都报） | 交付报告_P0_projections.md |
| `Trajectory.delete_geometry(obj 或 name)` | 禁用（R6′） | 几何明明在 `all_geometries` 里，仍报 `ValueError: 未分配几何至轨迹`，实际不可用 | 交付报告_P0-1b焊道单变量对比.md |
| `Project.delete_geometry(name)` | 禁用（R6′） | 删已挂载焊道后返回正常，但下一次 `import_geometry` 时 `FATAL PROGRAM ERROR: ACCESS VIOLATION` | 交付报告_P0-1b焊道单变量对比.md |
| `Trajectory.split_at` | 禁用（硬规则 4） | 清掉焊道分配和方向设置 | 项目早期交接记录（无公开交付报告） |
| `Trajectory.calculate_all_orientations` | 受限（R6′）：只在纯脚本新建的工程上、每条轨迹调用一次 | 轨迹的 `trajectory_modification` 块只由它生成，缺了就没有方向表和 NDSQ；在界面建的旧工程上先切 local-vector 再调用会 ACCESS VIOLATION；重复调用无必要且有同类风险 | 交付报告_A3纯脚本建模_进度.md；交付报告_任务C_calculate_all_projections.md；交付报告_A4纯脚本闭环.md |
| 同上，外表面焊缝线 | 必须 `search_radius=UnitValueLength(5, "mm")`（R17） | 外表面点离腹板和板有一段距离，默认搜索半径内没有组件，Simufact `ACCESS VIOLATION`；给 5 mm 后正常，保存的 `reference_volume` 半径与参照相同 | 交付报告_A4纯脚本闭环.md；交付报告_B4b_reference_volume.md |
| `WeldLine.import_points` | 导入后必须 `wl.orientation = "local-vector"`（R18） | CSV 表头 `0;1;0;0` 会把焊缝线设成 global-vector，随后方向计算报"方向计算不适合全局方向" | 交付报告_A4纯脚本闭环.md |
| 轨迹方向模式 / 变化角 / `connect_to_nodes`、接触表单个接触对 | 无 API：关掉 Simufact 离线改 XML 并自检（R7、R22） | API 文档里 Trajectory/Robot 没有这些属性；`build/patch_trajectory.py` 改 `robots_properties.xml`（Qt qCompress），`build/contact_table_gen.py` 按接触模板生成 `_contact_table/properties.xml` | 交付报告_A3纯脚本建模_进度.md；交付报告_任务B节点连接.md；交付报告_A4b接触表与ORIENT容差.md |
| `check_model` 的"节点连接未验证""固定时间步覆盖手动步长"告警 | 可忽略（R4、R10） | `.dat` 五个关键段不变 | 交付报告_任务B节点连接.md；交付报告_P0_projections.md |

已核实可用的调用序列见 `docs/simufact-api-notes.md`。

## HyperMesh 批处理 Tcl 规则（母材网格在工具外划分时）

| # | 规则 |
|---|---|
| TCL1 | 先划细的区域再划粗的，反过来会毁掉细网格 |
| TCL2 | mark 只能用 1 和 2 |
| TCL3 | `*readfile` / `*writefile` 前加 `hm_answernext yes` |
| TCL4 | `*solid_split_by_tool` 只有 `tool_type=plane` 可靠，用线或面当刀具会静默失效 |

## 判定标准

| 项 | 通过 |
|---|---|
| preflight | block = 0（warn 要在报告里列出） |
| prep | `01_prep/manifest.json` 全部 checks ok：R1、R2 detJ 非正 0、R8、R19、CSV 换行、10 µm 近重合区间 0 |
| gate | `check_short_v3` 结论"通过"或"通过（数值等价: ORIENT）"，exit 0；正式输入核对不带 `--allow` |
| run | 短跑 `--allow AUTO_STEP` 放行；`.sts` exit number 3004 且所有载荷工况跑到 100 %；求解器所用 `.dat` 与核对过的 `.dat` 除创建日期外相同 |
| run（警告，不判失败） | `04_run/solver_stats.txt` 里 `Run warnings` 为 none：没有翻转单元、没有被回退重试的增量、`.out` 已扫描 |
| post（有 `post` 块时） | 每个测点的最近节点在 `max_distance_mm` 以内；t8/5 要看窗口内样本数，样本太少就只报峰值 |
| compare | 同截面峰值温差 < `run.temperature_limit_percent`（默认 5%） |
| selfcheck | `run_regressions.py` 不一致 0；单元测试全过 |

**退出码 3004 不等于算干净。** 判 `run` 通过只看两件事：退出码 3004，且所有载荷工况进度到 100 %。其余（`.out` 里的
`element inside out`、被回退重试的增量）是警告，写进 manifest 和交付报告，但不判失败——求解器自己恢复并跑完的算例不许被
工具否掉。反过来也不许把警告当没有：一个正常结束的参照算例就可能 3004、100 %、焊根仍有一个单元翻转过（见
`docs/validation.md`）。`.sts` 里的 `pst` / `rst` 是后处理文件和重启文件的写入计数，跟着"每 N 增量写一次"的设置走，不是
求解器回退次数；真正的回退表现为增量号重复。

**测点按坐标定，不按节点号。** 两套网格的节点号没有对应关系，拿节点号跨网格比就是比了两个不同位置。`post.probes` 写物理
坐标，工具找最近节点并报出距离；距离超过 `max_distance_mm` 判失败，不许改大了了事——粗网格上最近节点离目标点三四毫米，
说明那套网格本来就代表不了这个点，该说的是这句话，不是把两个不同位置的峰值拿来比。

温差 5%、ORIENT 方向分量容差 2e-3（弧长 1e-5 m）、NDSQ 0.005 mm 这些阈值是验证轮次里定的经验值，不是物理定律：不要当铁律引用，也不许随手放宽。改阈值要同时改 `common/rules.yaml` 里对应规则（R20、R23）、代码里的实际常量（`gate/check_short_v3.py` 的 `ORIENT_TOL`、`ORIENT_ARC_TOL`、`NDSQ_TOL`；温差上限在 `case.json` 的 `run.temperature_limit_percent`），并跑 `python gate/run_regressions.py`，退出码 0 才算改完。

规则全表：`docs/rules.md`（由 `common/rules.yaml` 生成，改 yaml 后跑 `python tools/gen_rules_doc.py`）。

## 交付报告模板

每个任务结束都按这个写，文件名 `交付报告_<任务名>.md`，放在 AI 自己的输出目录。

```
# 交付报告：<任务名>  日期

## 结论（一句话：通过 / 未通过 / 部分通过）

## 通过标准逐条核对
| 标准 | 结果 | 证据文件与位置 |

## 产出文件清单（绝对路径）

## 执行记录
每一步：做了什么、跑了什么命令、关键输出原文（贴 10 行以内）

## 差异与异常
所有与预期不符的地方，原文报错，你的判断，是否已解决

## 没做的事和原因

## 给审核人的问题（最多 5 个）
```
