#!/usr/bin/env python3
"""E-4 土壤 Pb 结果核验计算编排。

输入只允许来自：
1) Playwright 从门户下载并校验过指纹的附件 JSON（browser/attachments/*.json）
2) 本地 materials（采样计划、控制值、现场记录、规程）
不直接读 portal_data 种子。所有换算调用 workbench.domain 原语，缺少必要条件即报错并停止该比较。
"""
import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.domain import dry, censored, custody, convert_unit  # noqa: E402

LIMIT = Decimal("50")  # materials/limits.csv：Pb 土壤 干基 50 mg/kg


def load_portal_fields(evidence_dir: Path) -> dict:
    out = {}
    for f in sorted((evidence_dir / "attachments").glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        out[payload["id"]] = payload
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence_dir", type=Path)
    ap.add_argument("materials_dir", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()

    records = load_portal_fields(args.evidence_dir)
    # 采样计划
    sampling = {}
    with (args.materials_dir / "sampling.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sampling[row["sample"]] = row

    bundle = {"project": "E-4", "limit": {"analyte": "Pb", "matrix": "土壤",
                                          "basis": "干基", "value_mgkg": str(LIMIT)},
              "sources": {"portal_evidence_dir": str(args.evidence_dir),
                          "materials": ["sampling.csv", "limits.csv", "field_notes.md",
                                        "policy.md"]},
              "custody_checks": [], "calculations": [], "stops": []}

    # ---------- 1. 交接链校验（原语 custody） ----------
    chain_events = {sid: [] for sid in sampling}
    for rid, rec in records.items():
        if rec["category"] == "交接":
            f = rec["fields"]
            chain_events.setdefault(f["sample"], []).append(
                {"order": f["order"], "sender": f["sender"],
                 "receiver": f["receiver"], "signed": f["signed"], "ref": rid})
    for sid, events in sorted(chain_events.items()):
        issues = custody([{k: e[k] for k in ("order", "sender", "receiver", "signed")}
                          for e in events], "赵甲")
        refs = sorted(e["ref"] for e in events)
        if not events:
            verdict = "无交接记录"
        elif issues:
            verdict = "断链：事件 " + "、".join(str(o) for o in issues) + \
                      "（发送人与上一持有人不匹配或缺签）"
        else:
            verdict = "连续完整（赵甲→…→实验室，均签署）"
        bundle["custody_checks"].append(
            {"sample": sid, "events": events, "issue_orders": issues,
             "verdict": verdict, "refs": refs})

    # ---------- 2. 分样血缘（不替代原样身份） ----------
    splits = []
    for rid, rec in sorted(records.items()):
        if rec["category"] == "分样":
            splits.append({"ref": rid, "parent": rec["fields"]["parent"],
                           "child": rec["fields"]["child"],
                           "method": rec["fields"]["method"]})
    bundle["splits"] = splits

    # ---------- 3. 逐报告计算 ----------
    def calc_record(rid):
        rec = records[rid]
        f = rec["fields"]
        item = {"ref": rid, "category": rec["category"], "sample": f["sample"],
                "analyte": f["analyte"], "reported": {k: f.get(k) for k in
                ("value", "lod", "basis", "unit", "dilution_applied", "replaces",
                 "reason") if k in f}, "steps": [], "comparable_to_limit": None,
                "bound_mgkg_dry": None, "stop_reason": None}

        # ND：区间式检出限，绝不以零填补
        if f["value"] == "ND":
            interval = censored("ND", f["lod"])
            item["steps"].append({"tool": "censored", "input": {"value": "ND",
                                  "lod": f["lod"]}, "output": interval,
                                  "note": "ND=[0,LOD)，上界不含；LOD 以报告基准与单位计"})
            if f["basis"] != "干基" or f["unit"] != "mg/kg":
                item["stop_reason"] = "ND 区间缺干湿基/单位换算路径，停止比较"
                bundle["stops"].append({"ref": rid, "reason": item["stop_reason"]})
                item["comparable_to_limit"] = False
                return item
            item["bound_mgkg_dry"] = {"low": interval["low"],
                                      "high": interval["high"],
                                      "high_inclusive": interval["high_inclusive"]}
            item["limit_relation"] = {
                "interval_vs_limit": "区间上界 LOD=%.6g 高于控制值 50，区间包含超限区"
                                     % interval["high"],
                "determination": "不能判定未超限（也不能判定超限），禁止写成未超限"}
            item["comparable_to_limit"] = True
            return item

        value = Decimal(str(f["value"]))
        # 先统一单位到 mg/kg
        if f["unit"] != "mg/kg":
            converted = convert_unit(value, f["unit"], "mg/kg")
            item["steps"].append({"tool": "convert_unit",
                                  "input": {"value": str(value), "unit": f["unit"],
                                            "target_unit": "mg/kg"},
                                  "output": converted})
            value = Decimal(converted)
        # 湿基 -> 干基
        if f["basis"] == "湿基":
            plan = sampling.get(f["sample"], {})
            moisture = plan.get("moisture") or ""
            if not moisture.strip():
                item["stop_reason"] = ("含水率缺失（%s 现场标签脱落，采样计划亦无值），"
                                       "湿基无法换算干基，停止与干基控制值比较"
                                       % f["sample"])
                bundle["stops"].append({"ref": rid, "reason": item["stop_reason"]})
                item["comparable_to_limit"] = False
                # 仍保留数学上下界（信息性，不构成判定）
                item["bound_mgkg_dry"] = {
                    "info_only": True,
                    "dry_equals_limit_when_moisture": str(1 - Decimal(str(f["value"])) / LIMIT),
                    "note": "dry=wet/(1-m)；m∈[0,1) 时 dry∈(wet,+∞)，需补含水率"}
                return item
            dried = dry(value, moisture)
            item["steps"].append({"tool": "dry",
                                  "input": {"value": str(value),
                                            "moisture": moisture},
                                  "output": dried,
                                  "formula": "%s/(1-%s)" % (value, moisture)})
            value = Decimal(dried)
        elif f["basis"] != "干基":
            item["stop_reason"] = "未知基准 %s，停止比较" % f["basis"]
            bundle["stops"].append({"ref": rid, "reason": item["stop_reason"]})
            item["comparable_to_limit"] = False
            return item

        item["bound_mgkg_dry"] = {"low": float(value), "high": float(value),
                                  "high_inclusive": True}
        item["limit_relation"] = {"value_mgkg_dry": str(value),
                                  "limit_mgkg_dry": str(LIMIT),
                                  "exceeds_limit": value > LIMIT,
                                  "margin": str(value - LIMIT)}
        item["comparable_to_limit"] = True
        if f.get("dilution_applied"):
            item["steps"].append({"note": "报告已计入稀释倍数，按规程不重复稀释；"
                                          "倍数数值未在门户记录，向实验室索取"})
        return item

    for rid in ["R1", "R2", "R3", "R3C", "R4", "R5"]:
        bundle["calculations"].append(calc_record(rid))

    # ---------- 4. 计划样本覆盖核对 ----------
    planned = sorted(sampling)
    reported_samples = set()
    for rec in records.values():
        if rec["category"] in ("报告", "补正"):
            reported_samples.add(rec["fields"]["sample"])
    # 分样映射回原样
    child_to_parent = {s["child"]: s["parent"] for s in splits}
    covered = set()
    for s in reported_samples:
        covered.add(child_to_parent.get(s, s))
    bundle["plan_coverage"] = [
        {"planned_sample": sid, "point": sampling[sid]["point"],
         "has_result_or_alias": sid in covered,
         "reporting_samples": sorted(s for s in reported_samples
                                     if child_to_parent.get(s, s) == sid),
         "gap": None if sid in covered else "无任何结果"}
        for sid in planned]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({"output": str(args.output),
                      "stops": len(bundle["stops"]),
                      "coverage_gaps": [c["planned_sample"] for c in
                                        bundle["plan_coverage"]
                                        if not c["has_result_or_alias"]]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
