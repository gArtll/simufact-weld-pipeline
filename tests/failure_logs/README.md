# 求解日志样本：PASS / PASS with warnings / FAIL

`run/solver_stats.py` 三种判定各有样本，`tests/test_solver_stats.py` 对它们做回归，防止判定漂移——尤其是把
"正常结束但过程中有翻转单元"误判成失败，或者把失败误判成通过。

| 文件前缀 | 判定 | 来源 | 退出码 | 进度 | 翻转单元 | 被回退重试的增量 |
|---|---|---|---|---|---|---|
| `synthetic_pass` | PASS | **合成**（`make_synthetic.py`） | 3004 | 100 % | 0 | 0 |
| `synthetic_pass_with_warnings` | PASS with warnings | **合成**（`make_synthetic.py`） | 3004 | 100 % | 1 | 1 |
| `synthetic_exit3015_a` | FAIL | **合成**（`make_synthetic.py`） | 3015 | 62.4 % | 10 | 0 |
| `synthetic_exit3015_b` | FAIL | **合成**（`make_synthetic.py`） | 3015 | 49.5 % | 10 | 4 |

四个 `synthetic_*` 都不是任何求解的日志：行格式与 Simufact/MARC 的 `.sts` / `.out` 相同，数值全部由
`make_synthetic.py` 按固定种子生成，文件头写明 `SYNTHETIC`。测试同时检查它们与生成器输出逐字相同。本仓库不保存
任何真实求解的日志。

## 合成样本复现的失效形态（3015 的常见特征，用于比对，不是结论）

1. **最后一条 error 是时间步到底**：`*** error - unable to reduce time step below minimum of ...`。
2. **inside out 报错成串**：单元号相邻成组，同一单元反复报告——局部塌陷，不是全局失稳。
3. **接触/分离类 warning 占绝大多数**：`Ignore recycling due to separation`。这只是计数；没有按增量号和接触对拆开、
   与翻转单元的位置和时刻对齐之前，不能据此断定接触设置是根因（方法见 `AGENTS.md` 的*失效定位方法*）。
4. **迭代次数在末尾抬起来**：`cyc1` 在最后几个增量急升。
5. **进度停在 100 % 以下**。

两个 PASS 样本里没有这些：0 条 error（PASS with warnings 那份有一个瞬时翻转单元，求解器回退后恢复），warning 以一次性提示为主。
