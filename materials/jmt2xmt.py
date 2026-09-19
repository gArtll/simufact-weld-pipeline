# -*- coding: utf-8 -*-
"""
JMatPro .jmt  ->  Simufact Welding .xmt   (直接生成, 不经 Simufact Material 界面)

做法: 拿一张已验证可用的 .xmt 当模板(结构/选择器/潜热字段齐全),
把里面所有数据表的数值换成新 .jmt 的, 再改标量与元数据.

用法:
  python jmt2xmt.py <in.jmt> <out.xmt> --template <已验证.xmt>
         --name E551T1-Ni1C --gb E551T1-Ni1C --ys 560
         --liquidus 1517.9 --solidus 1483.0 --latent 246321
         [--author "..."] [--selftest]

--selftest : 生成后把每张表与模板逐数值比对.
             用模板自己那张牌号的 .jmt 跑, 偏差应全为 0 -> 证明映射正确.

已核实的映射关系 (2026-08-31 会话核对):
  * .jmt 流变数据按「温度分组, 组内 7 个应变率」排列;
    .xmt 表名按应变率分组 -> 需要转置
  * 物性单位全部直通不换算: MPa / - / 1/C / g/cm3 / W/(m.K) / kJ/(kg.K)
  * YOUNGS_MODULUS 中数值为 0 的高温点(液态)在 .xmt 里被丢弃
  * EXPANSION_COEFF 起点是 30C 不是 25C (相对 25C 的平均值, 25C 处恒为 0)
  * 顶部标量 density 要 x1000 (g/cm3 -> kg/m3), 其余直通
"""
import io
import re
import sys
import argparse
import datetime
import xml.etree.ElementTree as ET

SEC2TAB = {
    "YOUNGS_MODULUS":  u"杨氏模量",      # 杨氏模量
    "POISSONS_RATIO":  u"泊松比",            # 泊松比
    "EXPANSION_COEFF": u"热膨胀系数",  # 热膨胀系数
    "THERMAL_COND":    u"热导率",            # 热导率
    "SPECIFIC_HEAT":   u"比热容",            # 比热容
    "DENSITY":         u"密度",                  # 密度
}

# .xmt 顶部标量 <tag> 取自哪张物性表的第一行, 以及倍率
SCALAR = [
    ("youngs_modulus",        "YOUNGS_MODULUS",  1.0),
    ("transverse_contraction", "POISSONS_RATIO",  1.0),
    ("thermal_expansion",     "EXPANSION_COEFF", 1.0),
    ("density",               "DENSITY",         1000.0),
    ("thermal_conductivity",  "THERMAL_COND",    1.0),
    ("specific_heat_capacity", "SPECIFIC_HEAT",   1.0),
]


def num(x):
    """按模板写法格式化: 整数值不带 .0"""
    f = float(x)
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def parse_jmt(path):
    lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    head = []
    i = 0
    while i < len(lines) and not lines[i].startswith("FLOW_STRESS"):
        head.append(lines[i])
        i += 1
    if i >= len(lines):
        sys.exit("x 没找到 FLOW_STRESS 段")

    ns, nr, nt = [int(v) for v in lines[i + 1].split()]
    strains = lines[i + 2].split()
    rates = lines[i + 3].split()
    temps = lines[i + 4].split()
    if not (len(strains) == ns and len(rates) == nr and len(temps) == nt):
        sys.exit("x 流变网格行长与 %d/%d/%d 不符" % (ns, nr, nt))
    i += 5

    flow = {}                                # (温度idx, 应变率idx) -> [ns 个应力字符串]
    for ti in range(nt):
        for ri in range(nr):
            row = lines[i].split()
            i += 1
            if len(row) != ns:
                sys.exit("x 流变数据第 %d 行长度 %d != %d" % (i, len(row), ns))
            flow[(ti, ri)] = row

    phys = {}                                # 段名 -> [(T, val字符串), ...] 升序
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        tok = s.split()
        if tok[0] not in SEC2TAB:
            sys.exit("x 未知物性段: %s" % tok[0])
        n = int(tok[3])
        i += 1
        pts = []
        for _ in range(n):
            a, b = lines[i].split()
            i += 1
            pts.append((float(a), b))
        pts.sort(key=lambda p: p[0])
        phys[tok[0]] = pts

    missing = set(SEC2TAB) - set(phys)
    if missing:
        sys.exit("x .jmt 缺物性段: %s" % ", ".join(sorted(missing)))
    return head, strains, rates, temps, flow, phys


ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("dst")
ap.add_argument("--template", required=True)
ap.add_argument("--name", default=None)
ap.add_argument("--gb", default=None)
ap.add_argument("--author", default=None)
ap.add_argument("--ys", type=float, default=None)
ap.add_argument("--liquidus", type=float, default=None)
ap.add_argument("--solidus", type=float, default=None)
ap.add_argument("--latent", type=float, default=None)
ap.add_argument("--nu-max", type=float, default=0.48,
                help="泊松比封顶值, 必须 < 0.49 (默认 0.48; Simufact 模型检查的硬性要求)")
ap.add_argument("--class", dest="cls", default="Welding",
                help="材料类别, Simufact Welding 只认 Welding (默认 Welding)")
ap.add_argument("--selftest", action="store_true")
args = ap.parse_args()
if args.nu_max >= 0.49:
    sys.exit("x --nu-max 必须小于 0.49")

head, strains, rates, temps, flow, phys = parse_jmt(args.src)
template_text = io.open(args.template, encoding="utf-8").read()
out = template_text

fT = [float(x) for x in temps]
fR = [float(x) for x in rates]
fS = [float(x) for x in strains]

FLOW_NAME = re.compile(r"^T=([\d.eE+-]+)\s*°C,phi_p=([\d.eE+-]+)\s*1/s$")


def ym_nonzero(pts):
    return [p for p in pts if float(p[1]) != 0.0]


def nu_clamp(pts):
    """泊松比封顶。JMatPro 在完全液态区给 nu->0.5(不可压缩), 物理没错但求解器
    体积锁死, Simufact 模型检查会报「泊松比过高, 应设置为 < 0.49」。
    库材料 HSLA-65_sw/DH36_sw 用常数 0.3, 16MnCr5-SPM_sw 的表最高 0.41。"""
    out, n = [], 0
    for x, y in pts:
        if float(y) > args.nu_max:
            out.append((x, num(args.nu_max)))
            n += 1
        else:
            out.append((x, y))
    if n:
        stat["nu_clamped"] = n
    return out


def rows_for(name):
    """按 .xmt 表名返回该表应有的 [(x, y字符串), ...]; 认不出返回 None"""
    m = FLOW_NAME.match(name)
    if m:
        t, r = float(m.group(1)), float(m.group(2))
        ti = min(range(len(fT)), key=lambda k: abs(fT[k] - t))
        ri = min(range(len(fR)), key=lambda k: abs(fR[k] - r))
        if abs(fT[ti] - t) > 1e-6 or abs(fR[ri] - r) / max(r, 1e-30) > 1e-9:
            return None
        return list(zip(fS, flow[(ti, ri)]))
    for sec, tab in SEC2TAB.items():
        if name == tab:
            pts = phys[sec]
            if sec == "YOUNGS_MODULUS":
                return ym_nonzero(pts)
            if sec == "POISSONS_RATIO":
                return nu_clamp(pts)
            return pts
    return None


stat = {"hit": 0, "miss": 0, "nu_clamped": 0}


def repl_table(m):
    indent, name, attr, body = m.group(1), m.group(2), m.group(3), m.group(4)
    pairs = rows_for(name)
    if pairs is None:
        stat["miss"] += 1
        sys.stderr.write("  ! 认不出的表: %s\n" % name)
        return m.group(0)
    stat["hit"] += 1

    row_indent = "        "
    mi = re.search(r'\n([ \t]*)<row nb="1">', body)
    if mi:
        row_indent = mi.group(1)
    tail_indent = re.search(r'\n([ \t]*)$', body)
    tail_indent = tail_indent.group(1) if tail_indent else "    "

    kept = re.sub(r'[ \t]*<row nb="\d+">[^<]*</row>\n?', "", body).rstrip("\n")
    rows = "\n".join('%s<row nb="%d">%s;%s</row>' % (row_indent, k, num(x), num(y))
                     for k, (x, y) in enumerate(pairs, 1))
    attr = re.sub(r'rows="\d+"', 'rows="%d"' % len(pairs), attr)
    return '%s<sfDataTable name="%s"%s>%s\n%s\n%s</sfDataTable>' % (
        indent, name, attr, kept, rows, tail_indent)


out = re.sub(r'(\s*)<sfDataTable name="([^"]*)"([^>]*)>(.*?)</sfDataTable>',
             repl_table, out, flags=re.S)
print("数据表: 替换 %d 张, 认不出 %d 张" % (stat["hit"], stat["miss"]))
if stat["nu_clamped"]:
    print("泊松比: %d 个液态区点封顶到 %s (Simufact 要求 < 0.49)"
          % (stat["nu_clamped"], num(args.nu_max)))
if stat["miss"]:
    sys.exit("x 有表没认出来, 中止")


def setscalar(tag, val):
    global out
    pat = r"(<%s(?:\s[^>]*)?>)[^<]*(</%s>)" % (tag, tag)
    if re.search(pat, out) is None:
        print("  ! 标量缺失, 跳过: %s" % tag)
        return
    out = re.sub(pat, lambda m: m.group(1) + val + m.group(2), out, count=1)


for tag, sec, mult in SCALAR:
    pts = ym_nonzero(phys[sec]) if sec == "YOUNGS_MODULUS" else phys[sec]
    setscalar(tag, num(float(pts[0][1]) * mult))

for tag, val in [("yield_strength", args.ys), ("melting_point", args.liquidus),
                 ("solidus_temperature", args.solidus), ("latent_heat", args.latent)]:
    if val is not None:
        setscalar(tag, num(val))

setscalar("class", args.cls)          # Simufact Welding 只认 <class>Welding</class>
if args.name:
    setscalar("name", args.name)
if args.gb:
    setscalar("gb_norm", args.gb)
if args.author:
    setscalar("author", args.author)
setscalar("import_date", datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
setscalar("min_temperature", num(min(fT)))
setscalar("max_temperature", num(max(fT)))
setscalar("min_strain_rate", num(min(fR)))
setscalar("max_strain_rate", num(max(fR)))
setscalar("min_effective_plastic_strain", num(min(fS)))
setscalar("max_effective_plastic_strain", num(max(fS)))

cmt = "\n".join(["Alloy calculated with JMatPro "] +
                [h for h in head if h.strip() and not h.startswith("Alloy calculated")])
setscalar("comment", cmt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

io.open(args.dst, "w", encoding="utf-8").write(out)
ET.fromstring(out[out.index("<sfMaterialData>"):])
print(u"OK 已写出 %s   (XML 解析通过)" % args.dst)

if args.selftest:
    def tables(txt):
        d = {}
        for m in re.finditer(r'<sfDataTable name="([^"]*)"[^>]*>(.*?)</sfDataTable>', txt, re.S):
            d.setdefault(m.group(1), []).append(
                [tuple(float(v) for v in r.split(";"))
                 for r in re.findall(r'<row nb="\d+">([^<]*)</row>', m.group(2))])
        return d

    A, B = tables(template_text), tables(out)
    worst, wname, bad = 0.0, "", 0
    for k in A:
        if k not in B:
            bad += 1
            print("  x 缺表 %s" % k)
            continue
        for ta, tb in zip(A[k], B[k]):
            if len(ta) != len(tb):
                bad += 1
                print("  x 行数不同 %s: %d vs %d" % (k, len(ta), len(tb)))
                continue
            for (x1, y1), (x2, y2) in zip(ta, tb):
                for u, v in ((x1, x2), (y1, y2)):
                    d = abs(u - v) / max(abs(u), abs(v), 1e-30)
                    if d > worst:
                        worst, wname = d, k
    print("自检: 表 %d 张, 结构错误 %d, 全表最大相对偏差 %.3g (%s)" % (len(A), bad, worst, wname))
    print("  " + ("OK 映射正确" if bad == 0 and worst < 1e-12 else "x 有偏差, 检查映射"))
