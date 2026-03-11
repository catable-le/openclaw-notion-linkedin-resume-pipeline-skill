# notion-linkedin-resume-pipeline

A reusable OpenClaw skill for automating the full **job-search → Notion tracking → resume generation** workflow.

## What this skill does

- Searches recent LinkedIn jobs (time-window based, e.g. last 24h)
- Applies configurable hard filters (location, role family, seniority, exclusions)
- Inserts accepted jobs into a Notion database in a structured format
- Generates tailored ATS-friendly resumes for `Not started` rows
- Updates row status and writes operational notes (`note` column)
- Produces a daily summary report for chat delivery

## Designed for

- Users who track applications in Notion
- Users who want repeatable, policy-based filtering (instead of manual scrolling)
- Users who want auto-generated, JD-aligned resume drafts

## Included components

- `SKILL.md`: Skill instructions and operating contract
- `scripts/run_pipeline.py`: Fixed deterministic pipeline script
- `references/onboarding-template.md`: Guided setup questions
- `references/config-schema.json`: Reusable config structure
- `references/report-template.md`: Daily report template

## Key safety controls

- Strict reject-before-insert gates (e.g., citizenship/PR-only, 3+ years, etc. when configured)
- Placeholder filename protection (`Company` / `Position` tokens are blocked)
- Retry policy (up to 5 attempts for metadata extraction issues)
- Failure traceability (writes cause to Notion `note` and report)
- No routine file deletions; explicit approval required for cleanup flows

## Setup overview

1. Prepare Notion integration token and database access.
2. Confirm required columns (`Name`, `Status`, `URL`, `position`, `note`).
3. Adjust filter rules in your runtime profile.
4. Run fixed script once manually.
5. Schedule with `openclaw cron` for daily automation.

## Example run

```bash
python3 skills/notion-linkedin-resume-pipeline/scripts/run_pipeline.py \
  --env /path/to/notion.env \
  --output-dir /path/to/generated_resumes \
  --max-accept 12
```

## Notes

- This repository intentionally excludes personal credentials, private resume content, and user-specific workspace memory files.
- Customize filters/formatting through your own config and secret files outside this repo.
