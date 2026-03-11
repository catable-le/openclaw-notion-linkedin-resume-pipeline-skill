#!/usr/bin/env python3
import os, re, json, time, argparse, requests
from pathlib import Path
from urllib.parse import quote
from html import unescape

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
    # Strictly extract JD body only; if missing, return empty so caller can mark parsing issue.
    m = re.search(r'<div class="show-more-less-html__markup[^>]*>([\\s\\S]*?)</div>', job_html)
    if not m:
        return ""
    txt = m.group(1)
    txt = re.sub(r"<script[\\s\\S]*?</script>", "", txt)
    txt = re.sub(r"<style[\\s\\S]*?</style>", "", txt)
    txt = txt.replace("<br>", "\\n").replace("<br/>", "\\n").replace("<br />", "\\n")
    txt = re.sub(r"</p>", "\\n\\n", txt)
    txt = re.sub(r"</li>", "\\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = unescape(txt)
    lines = [ln.strip() for ln in txt.splitlines()]
    # remove common non-JD metadata noise
    noisy = []
    for ln in lines:
        l = ln.lower()
        if not ln:
            noisy.append("")
            continue
        if re.search(r"\\b\\d+[+,]?\\s+applicants?\\b", l):
            continue
        if re.search(r"\\b(posted|reposted|\\d+\\s+(day|days|hour|hours|week|weeks)\\s+ago)\\b", l):
            continue
        if l in {"about the job", "job description"}:
            continue
        noisy.append(ln)
    txt = "\\n".join(noisy)
    txt = re.sub(r"\\n{3,}", "\\n\\n", txt).strip()
    if len(txt) > 18000:
        txt = txt[:18000] + "\\n\\n[Truncated]"
    return txt


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

    # scope of this script: LinkedIn search/filter + Notion insert + JD content retention only
    summary["script_scope"] = "linkedin_to_notion_jd_only"
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="/home/catable/.openclaw/workspace/.secrets/notion.env")
    ap.add_argument("--output-dir", default="/home/catable/.openclaw/workspace/generated_resumes")
    ap.add_argument("--max-accept", type=int, default=12)
    run(ap.parse_args())
