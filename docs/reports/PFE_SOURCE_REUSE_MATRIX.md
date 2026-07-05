# PFE Source Reuse Matrix (English Report)

## Purpose
This matrix defines what can be reused from existing material and what must be written originally for the final report.

## Reuse Policy
- Verified host company name for this report: Digital Virgo.
- Reuse company and sector facts only from previous same-company reports.
- Do not reuse technical solution text, implementation details, or result narratives from previous reports.
- All AdaptIQ solution chapters must be based on current project artifacts.

## Allowed vs Forbidden

| Source | Allowed Reuse | Forbidden Reuse |
|---|---|---|
| rapport/same company/*.pdf | Company profile facts, sector context, non-sensitive organization history | Technical architecture text, solution workflow, code explanations, test/result claims |
| rapport/another exemple of rapport/*.pdf | Writing flow, section sequencing, academic tone | Any direct paragraph copy, solution framing, diagrams as-is |
| rapport/my school template/Template_Latex_ISIMa/*.tex | Structure, chapter ordering, formatting conventions | Placeholder text copied without adaptation |
| cahier_des_charges_Adaptive_MCQ_platform.pdf | Initial requirement baseline, objective categories, original feature families | Treating this draft as final scope without documenting later company-driven changes |
| mhd/docs/architecture/SYSTEM_DOCUMENTATION.md | Architecture facts, stack, module boundaries, algorithm summary | Claiming features not present in current source |
| mhd/backend/README.md | Endpoint behavior, operational setup, security notes | Legacy routes/behavior marked outdated |
| mhd/docs/reports/FULL_SYSTEM_AUDIT_2026-04-14.md | Verified test outcomes and audit evidence | Copying whole findings text verbatim |

## Writing Rules for Originality
1. Write each chapter from scratch in English.
2. Use source-backed facts, then explain them in your own wording.
3. Every quantitative claim must map to a project source file.
4. Mark any unknown company detail as a placeholder until confirmed.
5. When initial specifications and final delivery differ, explicitly document the delta and rationale.
6. Do not state internal company facts unless they are validated by official project sources.

## Traceability Checklist
- Problem statement is linked to real platform limits addressed by AdaptIQ.
- Proposed solution maps to current architecture and active routes.
- Validation section cites measurable outcomes from audit/testing files.
- Difficulties and fixes are mapped to documented engineering changes.
