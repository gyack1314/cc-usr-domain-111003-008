#!/usr/bin/env python3
"""构建 result_lineage.json：样品→分样→报告/补正的血缘、三类判定与缺口。

数据来源（均为前序产物，不直接读种子）：
- state/verify/calc/calc_bundle.json   计算与交接校验结果
- state/verify/browser/collection_summary.json  门户页面出处与指纹
判定规则在本脚本中显式声明，数值不手工改写。输出内容寻址留版，重复运行幂等。
"""
import argparse
import hashlib
import json
from pathlib import Path

LEGEND = {
    "可比较": "交接链连续、分析方法可溯、单位/干湿基换算必要条件齐备，可与干基控制值 50 mg/kg 直接比较",
    "有限使用": "材料本身可溯但用途受限：或为不能支撑判定的检出区间、或为已被补正替代的历史结果、或采信有待补正确认",
    "留置待核": "存在交接断链或缺少换算必要条件，停止比较，补齐前不得用于现场处置的达标讨论",
}

# 显式判定表（理由经人工核验，对应 calc_bundle 中的客观状态）
CLASSIFICATION = {
    "R1": ("可比较",
           "交接 C1→C2 连续签署；湿基 42 有含水率 0.2 可换算干基；稀释倍数报告已计入，按规程不重复稀释。"
           "可确定干基 52.5 mg/kg，高于控制值。"),
    "R2": ("有限使用",
           "S2-A 分样血缘与交接完整，ND 保留为区间 [0,60) mg/kg 干基，不以零填补；但 LOD=60 高于控制值 50，"
           "区间跨越控制值，不能判定达标或超标，仅用于界定边界并触发更低检出限复测，不得写成未超限。"),
    "R3": ("有限使用",
           "已被 R3C 按 replaces 明确替代，仅保留来历与审计痕迹，不再作为现行依据；与 R2 来自不同分样，不得合并或平均。"),
    "R3C": ("有限使用",
            "为 R3C 指定替代 R3 的现行更正结果，干基 mg/kg、交接完整，数值 49 可与控制值比较；但‘录入更正’的原始依据与"
            "R3 正式撤回文件尚待实验室确认，且 49 与被替代值 54 分居控制值两侧、裕量仅 1 mg/kg，采信前须完成确认，"
            "确认前不得据此出具达标结论。"),
    "R4": ("留置待核",
           "双重阻断：C4 交接发送方为‘陌生承运’且未签署，与采样人赵甲不衔接，样品归属不能回到原样；"
           "且 S3 含水率标签脱落、采样计划无值，湿基 40 无法换算干基。补齐前停止比较。"),
    "R5": ("可比较",
           "交接 C5 连续签署；0.04 g/kg 经单位原语换算为 40 mg/kg 干基，必要条件齐备，可确定低于控制值。"
           "但分析方法未在门户记录，需实验室补充确认。"),
}

OPEN_QUESTIONS = [
    {"id": "Q1", "records": ["R1"], "target": "实验室",
     "question": "提供 R1 稀释倍数与稀释操作记录，确认 42 mg/kg 湿基已计入倍数、无需再除倍数，并提供原始读数与计算链。"},
    {"id": "Q2", "records": ["R2"], "target": "实验室",
     "question": "说明 S2-A 初测 LOD=60 mg/kg（高于控制值 50）的方法检出限/定量限评定与仪器条件；能否对 S2-A 或留存样以更低检出限复测。"},
    {"id": "Q3", "records": ["R3", "R3C"], "target": "实验室",
     "question": "提供 54→49‘录入更正’的原始记录、错误来源与审批，及 R3 的正式撤回/作废声明，确认 R3C 为 S2-B 唯一现行有效结果。"},
    {"id": "Q4", "records": ["R2", "R3C"], "target": "实验室",
     "question": "说明 S2-A 与 S2-B 两份分样结果差异的分样均匀性、取样量与质控情况（平行样、空白、加标回收）。"},
    {"id": "Q5", "records": ["C4", "R4"], "target": "采样组/实验室",
     "question": "补齐 S3 交接：C4 发送方‘陌生承运’与赵甲不衔接且未签，需补签或出具情况说明；含水率标签脱落，确认能否补测含水率或需重新采样。"},
    {"id": "Q6", "records": ["R5"], "target": "实验室",
     "question": "确认 R5 以 g/kg 出具的单位依据及 0.04 g/kg=40 mg/kg 换算，并说明与其他报告单位不一致的原因。"},
    {"id": "Q7", "records": ["R1", "R4", "R5"], "target": "实验室",
     "question": "补录 S1/S3/S4 的分析方法（门户仅 S2 两分样记录为消解ICP），确认方法一致性与适用标准。"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("calc_bundle", type=Path)
    ap.add_argument("browser_summary", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("version_dir", type=Path)
    args = ap.parse_args()

    bundle = json.loads(args.calc_bundle.read_text(encoding="utf-8"))
    browser = json.loads(args.browser_summary.read_text(encoding="utf-8"))
    page = {r["id"]: r for r in browser["records"]}
    calc = {c["ref"]: c for c in bundle["calculations"]}
    custody = {c["sample"]: c for c in bundle["custody_checks"]}

    def ref(rid: str) -> str:
        return page[rid]["reference"]

    def doc_sha(rid: str) -> str:
        return page[rid]["document"]["sha256"]

    def url(rid: str) -> str:
        return page[rid]["url"]

    nodes = []
    for rid in ["R1", "R2", "R3", "R3C", "R4", "R5"]:
        c = calc[rid]
        cls, reason = CLASSIFICATION[rid]
        node = {
            "node_id": rid,
            "record_ref": ref(rid),
            "portal_url": url(rid),
            "document_copy_sha256": doc_sha(rid),
            "sample": c["sample"],
            "analyte": c["analyte"],
            "reported": c["reported"],
            "status": "被替代（仅留存来历）" if rid == "R3" else (
                "补正现行结果（待确认）" if rid == "R3C" else "有效"),
            "replaced_by": "R3C" if rid == "R3" else None,
            "classification": cls,
            "adoption_reason": reason,
            "calc_steps": c["steps"],
            "comparable_to_limit": c["comparable_to_limit"],
            "bound_mgkg_dry": c["bound_mgkg_dry"],
            "limit_relation": c.get("limit_relation"),
            "stop_reason": c["stop_reason"],
            "open_question_ids": [q["id"] for q in OPEN_QUESTIONS
                                  if rid in q["records"]],
        }
        nodes.append(node)

    edges = [
        {"edge_id": "E1", "from": "S1", "to": "R1", "type": "原样→报告",
         "custody_refs": ["C1", "C2"], "custody_verdict": custody["S1"]["verdict"]},
        {"edge_id": "E2", "from": "S2", "to": "S2-A", "type": "分样",
         "ref": "A1", "record_ref": ref("A1"), "method": "消解ICP",
         "note": "分样不替代原样身份"},
        {"edge_id": "E3", "from": "S2", "to": "S2-B", "type": "分样",
         "ref": "A2", "record_ref": ref("A2"), "method": "消解ICP",
         "note": "分样不替代原样身份"},
        {"edge_id": "E4", "from": "S2-A", "to": "R2", "type": "分样→报告",
         "custody_refs": ["C3"], "custody_verdict": custody["S2"]["verdict"]},
        {"edge_id": "E5", "from": "S2-B", "to": "R3", "type": "分样→报告",
         "custody_refs": ["C3"], "custody_verdict": custody["S2"]["verdict"]},
        {"edge_id": "E6", "from": "R3", "to": "R3C", "type": "补正替代",
         "ref": "R3C", "record_ref": ref("R3C"),
         "scope": "R3C 仅替代其 replaces 指向的 R3 结果；R3 原始来历保留，不被抹掉"},
        {"edge_id": "E7", "from": "S2-B", "to": "R3C", "type": "分样→现行补正结果",
         "custody_refs": ["C3"]},
        {"edge_id": "E8", "from": "S3", "to": "R4", "type": "原样→报告（断链）",
         "custody_refs": ["C4"], "custody_verdict": custody["S3"]["verdict"]},
        {"edge_id": "E9", "from": "S4", "to": "R5", "type": "原样→报告",
         "custody_refs": ["C5"], "custody_verdict": custody["S4"]["verdict"]},
    ]

    lineage = {
        "schema_version": "1.0",
        "project": "E-4",
        "control_limit": bundle["limit"],
        "classification_legend": LEGEND,
        "planned_samples": {c["planned_sample"]: {
            "point": c["point"], "reporting_samples": c["reporting_samples"],
            "custody": custody[c["planned_sample"]]["verdict"],
            "custody_refs": custody[c["planned_sample"]]["refs"],
            "coverage_gap": c["gap"]}
            for c in bundle["plan_coverage"]},
        "result_nodes": nodes,
        "lineage_edges": edges,
        "custody_gaps": [
            {"sample": "S3", "ref": "C4", "record_ref": ref("C4"),
             "gap": "发送方‘陌生承运’≠上一持有人赵甲，且 signed=false；交接断链，样品归属不能回到原样",
             "remediation": "补签或出具情况说明，确认承运身份与取样人衔接"}
        ],
        "method_traceability_gap": [
            {"sample": "S1", "records": ["R1"], "gap": "门户未记录分析方法"},
            {"sample": "S3", "records": ["R4"], "gap": "门户未记录分析方法"},
            {"sample": "S4", "records": ["R5"], "gap": "门户未记录分析方法"},
            {"sample": "S2", "records": ["A1", "A2"], "method": "消解ICP",
             "gap": None},
        ],
        "conversion_gaps": bundle["stops"],
        "comparability_note_s2": (
            "R2（S2-A 初测）与 R3/R3C（S2-B 复测）是同一原样 S2 的两个独立分样，身份不互相替代；"
            "同点位、同名称、同报告日期均不构成合并理由，不得平均或择一。ND 的 [0,60) 区间与 49~54 的复测值"
            "在数学上可同时成立，因此南点不能给出单一达标/超标结论。"),
        "exceedance_bounds": {
            "S1_北点": {"determined": "干基 52.5 mg/kg，确定高于 50（裕量 +2.5）",
                      "caveat": "稀释倍数记录待 Q1 核验，数值已含稀释不重复计算"},
            "S2-A_南点初测": {"determined": "真值 ∈ [0,60) mg/kg 干基，区间跨越 50",
                           "caveat": "不能判定；需更低 LOD 复测（Q2）"},
            "S2-B_南点复测": {"determined": "被替代值 54（超）与现行补正值 49（低 1）分居控制值两侧",
                           "caveat": "补正依据待 Q3 确认，确认前不得判未超限"},
            "S3_西点": {"determined": "湿基 40；dry=40/(1-m)，m∈[0,1) 时干基 ∈ [40,+∞)",
                      "threshold": "m=0.2 时干基恰为 50；m>0.2 即超限，m<0.2 不超限",
                      "caveat": "含水率缺失且交接断链，不能判定，禁止写成未超限（Q5）"},
            "S4_东点": {"determined": "0.04 g/kg=40 mg/kg 干基，确定低于 50（裕量 -10）",
                      "caveat": "分析方法待 Q7 补录"},
        },
        "open_questions": OPEN_QUESTIONS,
        "plan_coverage_check": bundle["plan_coverage"],
        "statement_policy": (
            "本文件为内部核验血缘，不构成对外环境达标证明；‘不能判定’一律不得表述为‘未超限’；"
            "ND 保持检出限区间，不以零填补；草稿始终为待人工审批。"),
        "evidence_inputs": {
            "calc_bundle": str(args.calc_bundle),
            "browser_summary": str(args.browser_summary),
            "materials_manifest": "state/verify/evidence/materials-manifest.json"},
    }

    data = (json.dumps(lineage, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.version_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(data).hexdigest()
    args.output.write_bytes(data)
    versioned = args.version_dir / f"result_lineage.{sha[:12]}.json"
    status = "identical" if versioned.exists() else "new_version"
    versioned.write_bytes(data)
    print(json.dumps({"output": str(args.output), "sha256": sha,
                      "versioned": str(versioned), "status": status},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
