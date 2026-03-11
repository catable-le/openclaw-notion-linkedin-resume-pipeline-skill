#!/usr/bin/env python3
import os, re, json, time, argparse, requests, zipfile
from pathlib import Path
from urllib.parse import quote
from html import unescape
from xml.sax.saxutils import escape

ROLE_QUERIES = [
    "software engineer graduate", "software engineer junior",
    "data analyst graduate", "data analyst junior",
    "data scientist graduate", "data scientist junior",
    "ai engineer graduate", "ai engineer junior"
]

PLACEHOLDER_TOKENS = {"company", "position", "company name", "role"}


def load_env(env_path):
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k] = v.strip().strip('"').strip("'")


def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def ensure_note_column(db_id, h):
    db = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=h, timeout=30).json()
    props = db.get("properties", {})
    if "note" in props and props["note"].get("type") == "rich_text":
        return
    requests.patch(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=h,
        data=json.dumps({"properties": {"note": {"rich_text": {}}}}),
        timeout=30,
    )


def query_all_rows(db_id, h):
    out, cur = [], None
    while True:
        payload = {"page_size": 100}
        if cur:
            payload["start_cursor"] = cur
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=h, data=json.dumps(payload), timeout=30).json()
        out.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cur = r.get("next_cursor")
    return out


def title(prop):
    return "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()


def rich(prop):
    return "".join(x.get("plain_text", "") for x in prop.get("rich_text", [])).strip()


def is_placeholder(value):
    v = (value or "").strip().lower()
    return (not v) or any(tok == v or tok in v for tok in PLACEHOLDER_TOKENS)


def sanitize_filename(name):
    name = re.sub(r"[\\/:*?\"<>|]", "-", name)
    return re.sub(r"\s+", " ", name).strip()[:180]


def parse_job_cards(html):
    jobs = []
    for m in re.finditer(r"<li>(.*?)</li>", html, re.S):
        s = m.group(1)
        idm = re.search(r"jobPosting:(\d+)", s)
        hrefm = re.search(r'href="([^"]*linkedin\.com/jobs/view/[^"]+)"', s)
        titlem = re.search(r"<h3[^>]*>\s*(.*?)\s*</h3>", s, re.S)
        compm = re.search(r'job-search-card-subtitle"[^>]*>\s*(.*?)\s*</a>', s, re.S)
        if not (idm and hrefm and titlem and compm):
            continue
        jobs.append({
            "id": idm.group(1),
            "url": unescape(hrefm.group(1)).replace("&amp;", "&"),
            "position": re.sub(r"<[^>]+>", "", unescape(titlem.group(1))).strip(),
            "company": re.sub(r"<[^>]+>", "", unescape(compm.group(1))).strip(),
        })
    return jobs


def extract_jd_text(job_html):
    # Extract JD body only (not top-card metadata/sign-in prompts).
    matches = re.findall(r'<div class="show-more-less-html__markup[^>]*>([\\s\\S]*?)</div>', job_html)
    if not matches:
        return ""
    src = max(matches, key=len)
    txt = re.sub(r"<script[\\s\\S]*?</script>", "", src)
    txt = re.sub(r"<style[\\s\\S]*?</style>", "", txt)
    txt = txt.replace("<br>", "\\n").replace("<br/>", "\\n").replace("<br />", "\\n")
    txt = re.sub(r"</p>", "\\n\\n", txt)
    txt = re.sub(r"</li>", "\\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = unescape(txt)

    cleaned = []
    for ln in [x.strip() for x in txt.splitlines()]:
        l = ln.lower()
        if not ln:
            cleaned.append("")
            continue
        if any(m in l for m in ["join or sign in", "join to apply", "email or phone", "forgot password", "see who "]):
            continue
        if re.search(r"\\b\\d+[+,]?\\s+applicants?\\b", l):
            continue
        if re.search(r"\\b(posted|reposted|\\d+\\s+(day|days|hour|hours|week|weeks)\\s+ago)\\b", l):
            continue
        if l in {"about the job", "job description", "apply"}:
            continue
        cleaned.append(ln)

    txt = "\\n".join(cleaned)
    txt = re.sub(r"\\n{3,}", "\\n\\n", txt).strip()
    return txt[:18000]


def jd_to_children(jd_text):
    if not jd_text.strip():
        return []
    chunks = []
    for para in [p.strip() for p in jd_text.split("\\n\\n") if p.strip()]:
        while len(para) > 1800:
            chunks.append(para[:1800])
            para = para[1800:]
        chunks.append(para)
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]},
        }
        for c in chunks[:80]
    ]


def reject_reason(jd, position):
    txt = (jd + "\n" + position).lower()
    if any(x in txt for x in ["australian citizen", "must be an australian citizen", "permanent resident", "pr required"]):
        return "citizenship_pr"
    if re.search(r"\b([3-9]|\d{2,})\+?\s*years?\b", txt):
        return "exp_3plus"
    if "phd" in txt and ("required" in txt or "must" in txt):
        return "phd_only"
    if not any(x in position.lower() for x in ["software engineer", "data analyst", "data scientist", "ai engineer"]):
        return "non_target_role"
    if not any(x in position.lower() for x in ["graduate", "junior", "entry"]):
        return "non_target_seniority"
    return None


def make_docx(path, company, position):
    # Minimal deterministic writer with strict name inputs
    doc = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:w10="urn:schemas-microsoft-com:office:word" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14"><w:body>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="56"/></w:rPr><w:t>Candidate Name</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>PROFESSIONAL PROFILE</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>{escape(company)} - {escape(position)} tailored resume.</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>WORK EXPERIENCE</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>- Add role-specific, quantified achievements from your resume library.</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>KEY SKILLS SUMMARY</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>- Add JD-aligned technical and domain skills.</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>EDUCATIONAL BACKGROUND</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>- Include highest relevant degree(s).</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>RELEVANT PROJECT</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>- Add 1-2 JD-matched projects from your project bank.</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>PERSONAL ATTRIBUTES</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>- Add concise role-relevant strengths.</w:t></w:r></w:p>
</w:body></w:document>'''
    styles = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>'
    ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    doc_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)


def run(args):
    load_env(args.env)
    token = os.environ["TOKEN"]
    db_id = os.environ["DB_ID"]
    h = notion_headers(token)
    ensure_note_column(db_id, h)

    summary = {
        "searched": 0, "accepted": 0, "rejected": 0,
        "rejected_by_reason": {"citizenship_pr": 0, "exp_3plus": 0, "tech_mismatch_80": 0, "non_target_role": 0, "non_target_seniority": 0, "phd_only": 0, "other": 0},
        "added": 0, "resumes_generated": 0, "status_updated": 0,
        "failures": []
    }

    # LinkedIn search + strict reject (deterministic)
    seen = set()
    accepted_jobs = []
    for q in ROLE_QUERIES:
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={quote(q)}&location=Australia&f_TPR=r86400&sortBy=DD&start=0"
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25).text
        cards = parse_job_cards(html)
        summary["searched"] += len(cards)
        for j in cards:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            jd_html = requests.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{j['id']}", headers={"User-Agent": "Mozilla/5.0"}, timeout=25).text
            reason = reject_reason(jd_html, j["position"])
            if reason:
                summary["rejected"] += 1
                summary["rejected_by_reason"][reason] = summary["rejected_by_reason"].get(reason, 0) + 1
                continue
            j["jd_text"] = extract_jd_text(jd_html)
            accepted_jobs.append(j)
            summary["accepted"] += 1
            if len(accepted_jobs) >= args.max_accept:
                break
        if len(accepted_jobs) >= args.max_accept:
            break

    # insert accepted jobs to notion
    for j in accepted_jobs:
        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                "Name": {"title": [{"type": "text", "text": {"content": j["company"][:200]}}]},
                "URL": {"url": j["url"]},
                "position": {"rich_text": [{"type": "text", "text": {"content": j["position"][:2000]}}]},
                "Status": {"status": {"name": "Not started"}},
                "note": {"rich_text": [{"type": "text", "text": {"content": "accepted by strict filters"}}]}
            },
            "children": jd_to_children(j.get("jd_text", ""))
        }
        r = requests.post("https://api.notion.com/v1/pages", headers=h, data=json.dumps(payload), timeout=30).json()
        if r.get("object") == "page":
            summary["added"] += 1

    # generate resumes for Not started rows
    rows = query_all_rows(db_id, h)
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        p = row.get("properties", {})
        st = p.get("Status", {}).get("status", {}).get("name", "")
        if st != "Not started":
            continue
        company = title(p.get("Name", {}))
        position = rich(p.get("position", {}))

        ok = False
        fail_reason = None
        for _ in range(5):
            if is_placeholder(company) or is_placeholder(position):
                fail_reason = "placeholder_or_empty_company_position"
                # retry by re-reading current row fields
                company = title(p.get("Name", {}))
                position = rich(p.get("position", {}))
                time.sleep(0.2)
                continue
            fn = sanitize_filename(f"{company} + {position}.docx")
            if is_placeholder(fn):
                fail_reason = "placeholder_filename_detected"
                time.sleep(0.2)
                continue
            make_docx(outdir / fn, company, position)
            ok = True
            break

        if not ok:
            summary["failures"].append({"rowId": row["id"], "reason": fail_reason or "unknown"})
            note = f"resume generation failed after 5 retries: {fail_reason or 'unknown'}"
            requests.patch(f"https://api.notion.com/v1/pages/{row['id']}", headers=h, data=json.dumps({
                "properties": {"note": {"rich_text": [{"type": "text", "text": {"content": note[:1900]}}]}}
            }), timeout=30)
            continue

        summary["resumes_generated"] += 1
        requests.patch(f"https://api.notion.com/v1/pages/{row['id']}", headers=h, data=json.dumps({
            "properties": {
                "Status": {"status": {"name": "resume"}},
                "note": {"rich_text": [{"type": "text", "text": {"content": "resume generated by fixed script"}}]}
            }
        }), timeout=30)
        summary["status_updated"] += 1

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="./.secrets/notion.env")
    ap.add_argument("--output-dir", default="./generated_resumes")
    ap.add_argument("--max-accept", type=int, default=12)
    run(ap.parse_args())
