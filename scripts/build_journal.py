#!/usr/bin/env python3
"""向证据进度账（workbench.evidence.Journal）追加过程记录。

同键同内容幂等；同键不同内容由 Journal 抛错，不静默覆盖。
note 只记录来源地址、材料摘要、观察事实与未决问题，不认定业务有效性（判定在血缘文件中由规则给出）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.evidence import Journal  # noqa: E402


def main(db: str) -> None:
    j = Journal(db)
    # 血缘哈希若与最近一条血缘记录不同才新增修订键；相同则幂等复用，绝不重复
    lineage_bytes = Path(
        "state/verify/deliverables/result_lineage.json").read_bytes()
    lineage_sha = __import__("hashlib").sha256(lineage_bytes).hexdigest()
    prior = [n for n in j.read() if n["key"].startswith("11-lineage-output")]
    if prior and prior[-1]["note"].get("sha256") == lineage_sha:
        lineage_key = prior[-1]["key"]
        lineage_revision = prior[-1]["note"].get("revision", 1)
    else:
        lineage_revision = (max(n["note"].get("revision", 1) for n in prior) + 1
                            if prior else 1)
        lineage_key = ("11-lineage-output" if lineage_revision == 1
                       else f"11-lineage-output-r{lineage_revision}")
    browser = json.loads(Path(
        "state/verify/browser/collection_summary.json").read_text(encoding="utf-8"))
    calc = json.loads(Path(
        "state/verify/calc/calc_bundle.json").read_text(encoding="utf-8"))
    lineage = json.loads(Path(
        "state/verify/deliverables/result_lineage.json").read_text(encoding="utf-8"))
    page = {r["id"]: r for r in browser["records"]}

    def note(rid):
        r = page[rid]
        return {"record_ref": r["reference"], "portal_url": r["url"],
                "document_copy_sha256": r["document"]["sha256"],
                "attachment_sha256": r["attachment_json"]["sha256"],
                "sha_matches_page": r["attachment_json"]["sha_matches_page"]}

    entries = {
        "01-inputs": {
            "stage": "输入固定",
            "materials_manifest": "state/verify/evidence/materials-manifest.json",
            "portal_records_read_via_browser": len(browser["records"]),
            "index_pages": browser["index_pages"],
            "observation": "13 份门户记录经 Playwright 逐页阅读，附件 SHA-256 与页面摘要全部一致",
            "rule": "未直接读取 portal_data 种子"},
        "02-custody": {
            "stage": "交接链校验（domain.custody，初始持有人赵甲）",
            "results": [{k: c[k] for k in ("sample", "issue_orders", "verdict", "refs")}
                        for c in calc["custody_checks"]],
            "gap": "S3/C4 断链：陌生承运且未签",
            "open_question": "Q5"},
        "03-splits": {
            "stage": "分样血缘",
            "splits": calc["splits"],
            "rule": "复测分样不得替代原样身份；S2-A/S2-B 不合并"},
        "04-R1": {"stage": "R1 北点", **note("R1"),
                  "calc": "42 湿基 /(1-0.20)=52.5 干基 mg/kg；稀释已计入不重复",
                  "classification": "可比较", "open_question": "Q1"},
        "05-R2": {"stage": "R2 南点初测 S2-A", **note("R2"),
                  "calc": "ND 区间 [0,60) mg/kg 干基，LOD>控制值，不能判定",
                  "classification": "有限使用", "open_question": "Q2",
                  "rule": "ND 不以零填补，不写成未超限"},
        "06-R3": {"stage": "R3 南点复测 S2-B（历史）", **note("R3"),
                  "calc": "54 干基 mg/kg，超控制值",
                  "status": "被 R3C 按 replaces 替代，仅留存来历",
                  "classification": "有限使用"},
        "07-R3C": {"stage": "R3C 南点复测补正 S2-B（现行待确认）", **note("R3C"),
                   "calc": "49 干基 mg/kg，低于控制值但裕量仅 1",
                   "replaces": "R3",
                   "classification": "有限使用", "open_question": "Q3,Q4",
                   "rule": "补正只替代其指定结果，R3 来历不抹掉"},
        "08-R4": {"stage": "R4 西点", **note("R4"),
                  "stop": calc["stops"],
                  "bound": "干基∈[40,+∞)，临界含水率 m=0.2",
                  "classification": "留置待核", "open_question": "Q5,Q7"},
        "09-R5": {"stage": "R5 东点", **note("R5"),
                  "calc": "0.04 g/kg ×1000=40 mg/kg 干基，低于控制值 10",
                  "classification": "可比较", "open_question": "Q6,Q7"},
        "10-coverage": {
            "stage": "计划样本覆盖核对",
            "plan_coverage": calc["plan_coverage"],
            "observation": "S1-S4 均有结果或分样别名，无空白缺口；仅 S3 因断链不能回到原样"},
        lineage_key: {
            "stage": "结构化血缘产出",
            "path": "state/verify/deliverables/result_lineage.json",
            "sha256": lineage_sha,
            "revision": lineage_revision,
            "supersedes": None if lineage_revision == 1
            else "11-lineage-output" + ("" if lineage_revision == 2
                                        else f"-r{lineage_revision - 1}"),
            "change_reason": None if lineage_revision == 1
            else "单位换算原语输出固定为定点小数（4E+1→40），血缘重新生成，旧版保留于 versions/",
            "edges": len(lineage["lineage_edges"]),
            "nodes": [n["node_id"] + ":" + n["classification"]
                      for n in lineage["result_nodes"]]},
        "12-drafts": {
            "stage": "门户内部草稿（待人工审批）",
            "drafts": [
                {"key": "E4-verify-memo",
                 "url": "http://127.0.0.1:8765/draft/E4-verify-memo",
                 "refs": 13},
                {"key": "E4-lab-questions",
                 "url": "http://127.0.0.1:8765/draft/E4-lab-questions",
                 "refs": 9}],
            "readback": "回读状态均为待人工审批，正文与引用一致；R3 在草稿中明确标注被 R3C 替代，未作为最终依据"},
    }

    for key, payload in entries.items():
        seq = j.append(key, payload)
        print(f"{key} -> seq {seq}")
    print("total notes:", len(j.read()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "state/verify/evidence/journal.sqlite3")
