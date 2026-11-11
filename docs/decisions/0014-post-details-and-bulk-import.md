# 0014: Compact post details and atomic scheduled-post CSV import

- **Status:** Accepted
- **Date:** 2026-11-09

## Context

Calendar entries currently open the full post editor, and scheduling many custom posts requires
repeating that workflow. Bulk imports must use the existing post validation, persistence and
scheduler so imported content does not bypass platform or time checks.

## Options considered

1. **Import directly in the browser:** parse CSV and create posts row by row. This duplicates parsing
   and validation and can leave a partly imported batch.
2. **Backend preview and batch import:** validate the CSV on the server, require an explicit second
   request to create it, and insert the full batch in one database transaction.

## Decision

Calendar entries open a compact native details dialog; editing remains in the existing composer.
Scheduled posts can be imported from an Excel workbook (.xlsx, the template's format) or CSV (`body`, `scheduled_at`, semicolon-separated `platforms`,
optional `blog_url`). Because the files come from spreadsheets, the reader accepts what spreadsheets
write: times without an offset (read in the browser's IANA timezone, sent as `tz`, so daylight saving is
handled per post date), platform display names, any header case, `;` as the column separator and
Windows-1252 text. Excel date cells arrive as serial numbers and are converted. Workbooks are read
with the existing standard-library reader (now `services/xlsx.read_xlsx`), so no new dependency. The backend validates every row against the existing post rules,
then imports the batch atomically into the same planner tables and scheduler queue.

## Consequences

- A failed row prevents all rows from being imported; preview never changes stored data.
- Imported posts remain scheduled and use the normal background publisher. Import does not publish
  anything immediately.
- The CSV format intentionally covers the common text, time, platform and blog-link fields; platform
  variants and advanced attachment options remain editable in the post composer.
