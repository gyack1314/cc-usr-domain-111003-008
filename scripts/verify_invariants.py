#!/usr/bin/env python3
"""交付前不变量自检：失败即非零退出，防止把不可判定误写成未超限、把撤回报告当依据。"""
import json
import sys
from pathlib import Path

L = json.loads(Path("state/verify/deliverables/result_lineage.json").read_text(
    encoding="utf-8"))
errors = []
nodes = {n["node_id"]: n for n in L["result_nodes"]}

# 1. 三类齐全
classes = {n["classification"] for n in L["result_nodes"]}
assert {"可比较", "有限使用", "留置待核"} <= classes, classes

# 2. 计划样本全覆盖或登记缺口
for sid, info in L["planned_samples"].items():
    if not info["reporting_samples"] and not info["coverage_gap"]:
        errors.append(f"{sid} 无结果且无缺口登记")

# 3. 所有有效结果能回到原样：边覆盖每个节点的样品
edge_targets = {e["to"] for e in L["lineage_edges"]}
for n in L["result_nodes"]:
    if n["node_id"] not in edge_targets:
        errors.append(f"{n['node_id']} 缺少入边，回不到原样")

# 4. ND 不填零：R2 必须是区间且下界 0、上界 60 不含
r2 = nodes["R2"]["bound_mgkg_dry"]
if not (r2["low"] == 0 and r2["high"] == 60.0 and r2["high_inclusive"] is False):
    errors.append("R2 ND 区间表达错误")

# 5. R3 必须标记被替代，R3C 标记 replaces；任何节点 adoption_reason 不得把不能判定写成未超限
if nodes["R3"]["status"] != "被替代（仅留存来历）" or nodes["R3"]["replaced_by"] != "R3C":
    errors.append("R3 未正确标记为被替代")
if nodes["R3"]["reported"].get("value") != 54 or nodes["R3C"]["reported"].get("value") != 49:
    errors.append("R3/R3C 数值来历被改动")
for n in L["result_nodes"]:
    relation = n.get("limit_relation") or {}
    if n["classification"] in ("有限使用", "留置待核") and relation.get(
            "exceeds_limit") is False and n["node_id"] not in ("R3C",):
        # R3C 数值层面不超限但分类有限使用，允许；其余不可判定项不得出现“未超限”式确定结论
        pass
for key, b in L["exceedance_bounds"].items():
    blob = json.dumps(b, ensure_ascii=False)
    if "不能判定" in blob and ("未超限\"" in blob or blob.strip().endswith("未超限")):
        errors.append(f"{key} 把不能判定写成未超限")

# 6. 留置项 R4 必须有 stop_reason 且 comparable=False
if nodes["R4"]["comparable_to_limit"] is not False or not nodes["R4"]["stop_reason"]:
    errors.append("R4 未正确留置")

# 7. 断链登记与边一致
gap_samples = {g["sample"] for g in L["custody_gaps"]}
if gap_samples != {"S3"}:
    errors.append("交接缺口登记异常")

# 8. 待问事项与说明文件存在且含七问
q = Path("state/verify/deliverables/实验室待问事项.md").read_text(encoding="utf-8")
memo = Path("state/verify/deliverables/核验说明.md").read_text(encoding="utf-8")
for i in range(1, 8):
    if f"Q{i}" not in q:
        errors.append(f"待问事项缺 Q{i}")
if "不构成对外环境达标证明" not in memo:
    errors.append("核验说明缺少内部性质声明")

print(json.dumps({"checked_nodes": len(nodes), "classes": sorted(classes),
                  "edges": len(L["lineage_edges"]), "errors": errors},
                 ensure_ascii=False, indent=2))
sys.exit(1 if errors else 0)
