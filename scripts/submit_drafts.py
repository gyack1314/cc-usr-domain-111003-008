#!/usr/bin/env python3
"""通过门户浏览器表单保存内部核验草稿，并回读校验。

- 草稿正文取自 deliverables/ 下的 Markdown；引用取自门户采集摘要中的 编号@SHA256
- 已存在且内容一致：不产生新版本（门户幂等）；内容不同则必须先回读当前版本再修订，拒绝盲覆盖
- 保存后回读 /draft/<key> 与 /drafts，确认状态为“待人工审批”、正文与引用一致
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DRAFTS = [
    {"key": "E4-verify-memo", "title": "E-4 土壤Pb结果内部核验说明（待人工审批）",
     "memo": "核验说明.md", "ref_ids": "ALL"},
    {"key": "E4-lab-questions", "title": "E-4 实验室待问事项（内部）",
     "memo": "实验室待问事项.md",
     "ref_ids": ["R1", "R2", "R3", "R3C", "R4", "R5", "C4", "A1", "A2"]},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("deliverables_dir", type=Path)
    ap.add_argument("browser_summary", type=Path)
    ap.add_argument("screenshot_dir", type=Path)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    summary = json.loads(args.browser_summary.read_text(encoding="utf-8"))
    refs = {r["id"]: r["reference"] for r in summary["records"]}
    all_ids = list(refs)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()

        for d in DRAFTS:
            body = (args.deliverables_dir / d["memo"]).read_text(encoding="utf-8")
            wanted = all_ids if d["ref_ids"] == "ALL" else d["ref_ids"]
            ref_lines = "\n".join(refs[i] for i in wanted)

            # 先回读已存草稿，拿到当前版本，避免用过时 version 盲写导致 409
            page.goto(f"{base}/draft/{d['key']}", wait_until="domcontentloaded")
            main_text = page.locator("main").inner_text()
            if "草稿不存在" in main_text or page.get_by_test_id("draft-status").count() == 0:
                version = 0
                page.goto(f"{base}/draft/new", wait_until="domcontentloaded")
                page.get_by_label("草稿编号").fill(d["key"])
            else:
                existing_body = page.get_by_label("正文", exact=True).input_value()
                if existing_body == body:
                    results.append({"key": d["key"], "action": "already_current"})
                    continue
                version = int(page.locator("input[name='version']").input_value())

            page.get_by_label("标题", exact=True).fill(d["title"])
            page.get_by_label("正文", exact=True).fill(body)
            page.get_by_label("资料引用，每行一项 编号@摘要").fill(ref_lines)
            # version 为 hidden 字段，用 JS 赋值（新建 0，修订为已读版本）
            page.eval_on_selector("input[name='version']",
                                  "(el,v)=>{el.value=v;}", str(version))
            page.get_by_role("button", name="保存预审草稿").click()
            # 保存成功为 303 重定向到 /draft/<key>；等待重定向落地与状态元素出现
            page.wait_for_url("**/draft/**", timeout=15000)
            page.get_by_test_id("draft-status").wait_for(state="visible",
                                                          timeout=15000)

            # 回读校验
            status = page.get_by_test_id("draft-status").inner_text().strip()
            read_back_body = page.get_by_label("正文", exact=True).input_value()
            read_back_refs = page.get_by_label(
                "资料引用，每行一项 编号@摘要").input_value()
            assert status == "待人工审批", status
            assert read_back_body == body, "正文回读不一致"
            assert read_back_refs.strip() == ref_lines.strip(), "引用回读不一致"
            page.screenshot(path=str(args.screenshot_dir / f"{d['key']}.png"),
                            full_page=True)
            results.append({"key": d["key"], "action": "saved", "status": status,
                            "url": page.url})

        # /drafts 总览回读，确认两份草稿在册且无重复
        page.goto(f"{base}/drafts", wait_until="domcontentloaded")
        page.screenshot(path=str(args.screenshot_dir / "drafts_overview.png"),
                        full_page=True)
        listing = page.locator("main").inner_text()
        for d in DRAFTS:
            assert d["title"] in listing, f"草稿 {d['key']} 未在列表中"
            # 链接 href 指向草稿键，确认两份各一条、无重复
            assert page.locator(f"a[href='/draft/{d['key']}']").count() == 1, \
                f"草稿 {d['key']} 重复或缺失"
        browser.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
