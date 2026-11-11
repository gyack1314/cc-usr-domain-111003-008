#!/usr/bin/env python3
"""通过 Playwright 阅读本地门户全部记录，留存页面出处与附件指纹。

只做只读浏览与证据落盘：
- 每个记录访问 /record/<id>，保存截图、页面正文、引用编号@SHA256
- 通过页面下载 /attachment/<id>.json 与 /document/<id>，校验字节指纹
- 重复运行：同内容幂等跳过；内容变化不静默覆盖，写入 conflict/ 待人工判断
不直接读取 portal_data 种子文件。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

RECORD_IDS = ["C1", "C2", "C3", "C4", "C5", "A1", "A2",
              "R1", "R2", "R3", "R3C", "R4", "R5"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_content_addressed(path: Path, data: bytes, conflict_dir: Path) -> str:
    """同内容跳过；不同内容转存显式 conflict/ 目录，返回状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == data:
            return "identical"
        conflict_dir.mkdir(parents=True, exist_ok=True)
        n = 1
        while True:
            alt = conflict_dir / f"{path.name}.{n}.changed"
            if not alt.exists():
                alt.write_bytes(data)
                return "conflict"
            n += 1
    path.write_bytes(data)
    return "new"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    out = args.out_dir
    shots, atts, docs = (out / "screenshots", out / "attachments", out / "documents")
    index_dir = out / "index"
    for d in (shots, atts, docs, index_dir):
        d.mkdir(parents=True, exist_ok=True)

    summary = {"base_url": base, "records": [], "index_pages": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  accept_downloads=True)
        page = ctx.new_page()

        # 目录页（分页，每页 5 条；13 条共 3 页）
        page_no = 1
        while True:
            url = f"{base}/?page={page_no}"
            page.goto(url, wait_until="domcontentloaded")
            count_text = page.get_by_test_id("count").inner_text()
            rows = page.locator("tr[data-record-id]").all()
            page.screenshot(path=str(index_dir / f"index_p{page_no}.png"), full_page=True)
            ids = [r.get_attribute("data-record-id") for r in
                   page.locator("tr[data-record-id]").all()]
            summary["index_pages"].append(
                {"url": url, "count_text": count_text, "visible_ids": ids})
            # 分页导航存在即继续，直到没有下一页
            nav_links = page.locator("nav[aria-label='资料分页'] a").all()
            if page_no >= len(nav_links):
                break
            page_no += 1

        for rid in RECORD_IDS:
            rec_url = f"{base}/record/{rid}"
            page.goto(rec_url, wait_until="domcontentloaded")
            page.screenshot(path=str(shots / f"{rid}.png"), full_page=True)
            record = {
                "id": rid,
                "url": rec_url,
                "title": page.title(),
                "reference": page.get_by_test_id("reference").inner_text().strip(),
                "digest_shown": page.get_by_test_id("digest").inner_text().strip(),
                "fields_text": page.get_by_test_id("fields").inner_text(),
                "main_text": page.locator("main").inner_text(),
            }
            shown_ref_id, _, shown_sha = record["reference"].partition("@")
            assert shown_ref_id == rid, f"引用编号不一致: {rid}"

            # 下载原始 JSON 附件并校验指纹与页面一致
            with page.expect_download() as ev:
                page.get_by_role("link", name="下载原始材料").click()
            json_path = atts / f"{rid}.json"
            ev.value.save_as(str(json_path))
            json_bytes = json_path.read_bytes()
            json_sha = sha256_bytes(json_bytes)
            record["attachment_json"] = {
                "url": f"{base}/attachment/{rid}",
                "path": str(json_path.relative_to(out)),
                "sha256": json_sha,
                "sha_matches_page": json_sha == shown_sha,
            }
            payload = json.loads(json_bytes.decode("utf-8"))
            record["category"] = payload["category"]
            record["revision"] = payload["revision"]
            record["fields"] = payload["fields"]

            # 下载文书副本（不同对象，单独留指纹）
            with page.expect_download() as ev2:
                page.get_by_role("link", name="下载文书或扫描副本").click()
            doc_name = ev2.value.suggested_filename
            doc_path = docs / doc_name
            ev2.value.save_as(str(doc_path))
            doc_bytes = doc_path.read_bytes()
            record["document"] = {
                "url": f"{base}/document/{rid}",
                "path": str(doc_path.relative_to(out)),
                "suggested_name": doc_name,
                "sha256": sha256_bytes(doc_bytes),
                "size": len(doc_bytes),
            }

            page_json = out / "pages" / f"{rid}.json"
            status = write_content_addressed(
                page_json,
                (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
                 + "\n").encode("utf-8"), out / "conflict")
            record["evidence_status"] = status
            summary["records"].append(record)

        browser.close()

    page_statuses = {r["id"]: r.pop("evidence_status") for r in summary["records"]}
    # 汇总仅含稳定证据；同门户内容重复运行必须字节一致
    status = write_content_addressed(
        out / "collection_summary.json",
        (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2)
         + "\n").encode("utf-8"), out / "conflict")
    run_report = {"summary_file_status": status, "page_file_status": page_statuses}
    (out / "last_run_report.json").write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = all(r["attachment_json"]["sha_matches_page"] for r in summary["records"])
    print(json.dumps({"records": len(summary["records"]),
                      "index_pages": len(summary["index_pages"]),
                      "all_sha_match": ok,
                      "summary_status": status,
                      "page_status": page_statuses}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
