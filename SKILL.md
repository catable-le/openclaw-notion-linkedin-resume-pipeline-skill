---
name: notion-linkedin-resume-pipeline
description: "Configure and run a reusable end-to-end job-hunt pipeline: collect jobs from LinkedIn with user-defined filters, write rows/pages into a Notion database, generate tailored ATS resumes from a resume knowledge base, and update status/note fields. Use when a user asks to automate recurring job search + Notion tracking + resume generation workflows, including onboarding questions and per-user configuration."
---

# Notion LinkedIn Resume Pipeline

Build a reusable workflow that can be configured per user and then run daily/weekly.

## Quick workflow

1. Collect user configuration once.
2. Validate data sources (Notion DB, job source reachability, resume library).
3. Save config to a profile file.
4. Run fixed script once in dry-run/limited mode.
5. Schedule recurring execution via `openclaw cron` using the fixed script.
6. Post completion summary (counts + failures + notes), not attachments unless requested.

## Fixed script (must use)

Primary script:
- `scripts/run_pipeline.py`

Usage:
- `python3 scripts/run_pipeline.py --env ./.secrets/notion.env --output-dir ./generated_resumes --max-accept 12`

If script execution fails, debug from error reason and rerun in the same turn.

## Filename safety + retry rules (mandatory)

1. Never write resume files with placeholder tokens (e.g., `Company`, `Position`).
2. Validate `company` and `position` before generation; empty/placeholder values are immediate failures.
3. On placeholder/metadata failure, retry extraction/regeneration up to **5 attempts**.
4. If still failing after 5 attempts, write failure reason into Notion `note` and include it in the daily report.

## Step 1) Collect onboarding config (ask these in order)

Use this exact checklist during setup.

### A. Destination and state

- Notion database/page link
- Required columns and types (minimum: `Name`, `Status`, `URL`, `position`, `note`)
- Status workflow values (example: `Not started` -> `resume`)
- Whether to create child page content with full JD text

### B. Job search policy

- Sources (LinkedIn only / LinkedIn+Seek etc.)
- Time window (example: last 24h)
- Location policy (country + city priority)
- Target role families (example: Software Engineer, Data Analyst, Data Scientist, AI Engineer)
- Seniority policy (graduate/junior)
- Exclusion policy (citizenship/PR-only, years of experience thresholds, degree restrictions)
- Tech-stack fit threshold rule (example: reject when mismatch >= 80%)

### C. Resume generation policy

- Resume knowledge base location (files/folders)
- Mandatory sections and section order
- Formatting rules (font, sizes, separators, bullet/dash style, line layout)
- Naming format for generated files (example: `Company - Position Resume.docx`)
- Delivery policy (attach files vs summary-only)

### D. Automation and reporting

- Schedule (cron + timezone)
- Reporting destination (channel/user)
- Required daily summary fields:
  - searched count
  - filtered-in count
  - inserted count
  - resumes generated count
  - status updated count
  - failures with reasons

## Step 2) Validate prerequisites

Before first run:

- Verify Notion token and DB access.
- Verify required Notion columns; create missing `note` rich_text column.
- Verify job source access limitations (captcha/403) and define fallback behavior.
- Verify resume source files are readable.

If any prerequisite fails, stop and return a setup report with exact fixes.

## Step 3) Persist user profile config

Store config in a reusable profile file (JSON or markdown) under workspace, e.g.:

- `profiles/job-pipeline/<user>.json`

Include:

- source filters
- exclusion rules
- resume formatting template
- Notion field mapping
- schedule/report destination

## Step 4) Execution contract (per run)

Run in this order:

1. Search jobs by time window.
2. Apply all hard filters.
3. Insert accepted jobs into Notion.
4. Traverse Notion rows where `Status=Not started`.
5. Generate tailored resume per row.
6. If success, set `Status=resume`; write remarks to `note`.
7. Post summary report.

Always write operational notes to `note` when something is unusual (missing company values, restricted eligibility, extraction issues, anti-bot block, JD ambiguity).

## Step 5) Resume tailoring rules

For each role:

- Map JD keywords to candidate evidence from resume knowledge base.
- Rewrite Professional Profile to combine:
  - role requirements
  - company value/culture signals (from official pages when available)
  - candidate achievements
- Keep ATS-safe language and measurable outcomes.
- Keep concise (1-2 pages unless user overrides).

## Step 6) Scheduling

Use `openclaw cron add` with:

- timezone set explicitly
- isolated session preferred for repeatability
- announce summary only unless user asks for attachments

If schedule changes, edit job instead of creating duplicates.

## References

- Onboarding questionnaire template: `references/onboarding-template.md`
- Config schema template: `references/config-schema.json`
- Daily run report template: `references/report-template.md`
