# SheetCode

[![CI](https://github.com/tommytty/SheetCode/actions/workflows/ci.yml/badge.svg)](https://github.com/tommytty/SheetCode/actions/workflows/ci.yml)

An MCP server that lets Claude read and update a Google Sheet that tracks
LeetCode problems on a spaced-repetition schedule.

## How it works

```
Claude Code (MCP client)
    |  JSON-RPC over stdio
    v
mcp_server/server.py   four MCP tools, each a thin wrapper
    |
    v
sheets/client.py       SheetsClient: all reads, writes and logging
    |
    v
Google Sheets API v4   (service account auth in sheets/auth.py)
```

`sheets/` has no knowledge of MCP, and `mcp_server/` has no knowledge of Google
auth. The client could sit behind a CLI or another protocol without changes.

## Design

**Claude does all the scheduling reasoning. The server does none of it.**
There is no interval calculation, date arithmetic or "next interval" function
in the code. Claude reads the current row, decides the new `next_review` and
`interval_days`, and passes the final values in. The server reads cells,
writes cells, and owns two mechanical jobs that are easy to get wrong and
gain nothing from judgment:

- **Finding a row.** Writes take a slug, not a row number or A1 range. The
  server looks the slug up at the moment of the write. The sheet gets sorted
  by hand in the browser, so any row number Claude saw earlier can be stale
  by the time it writes. Looking up by slug means a sort can't redirect a
  write to the wrong problem.
- **Choosing where a new row goes.** `append_row` uses the Sheets append
  endpoint, so the end of the data is found by the API, not by Claude.
  Writing past the end of a sheet is legal and silently grows it, so a wrong
  guess would leave a gap rather than fail.

Columns are also addressed by header name, never by fixed index. The header
row is read on each call and matched case-insensitively.

**Logging is automatic.** Every `append_row` and `update_cells` call writes
an entry to an append-only `log` tab inside the same tool call: timestamp,
slug, action (`added`, `completed`, `skipped`, `edited`), interval before and
after, and next review date. Claude is never asked to log as a separate step,
because a model would sometimes forget, and because a log written by the
model records what it says it did rather than what the code actually wrote.
If the row write succeeds but the log write fails, the tool returns a
`warning` instead of raising, so Claude reports it rather than retrying and
writing the row twice.

**Writes return before and after state.** `update_cells` reads the row, writes
it, then reads it back, and returns both versions. `after` comes from the sheet,
not from the request, so it doubles as verification. `before` is what lets
Claude say "interval 17 -> 19" in its confirmation. Once the write happens,
the old value is gone, so it has to be captured first.

## Why MCP instead of a CLI

A CLI with the scheduling rules written in plain code would be simpler, and
for pure date bookkeeping it would be the better tool. MCP earns its place
when the tools combine with judgment the code can't supply. For example,
Claude can find problems with blank complexity columns, work out the time
and space complexity of each from the problem itself, and write them back
with the same tools it uses for scheduling. Exceptions to the schedule, such
as treating a particular problem differently after a reset, are things Claude
can apply from conversation. A CLI would need a new flag for each one.

## Tools

| Tool | What it does |
|------|--------------|
| `get_schema()` | Lists each tab's headers with their column letters, plus the first data row as a sample of value formats. Call it first. |
| `read_range(range_str)` | Reads an A1 range. Includes a note when the range is empty or extends past the data. |
| `append_row(tab, values)` | Adds a problem at the end of a tab from a header-to-value map, and logs an `added` entry. |
| `update_cells(tab, slug, fields, action)` | Updates named fields on the row with that slug, logs the given action, and returns the row before and after. |

## Example usage

These are illustrative descriptions of how a session goes, not captured
transcripts.

**"What's due today?"**
Claude calls `get_schema` to learn the layout, then `read_range("Sheet1!A2:F")`
to read every row. The server returns values only. Claude compares each
`next_review` against today's date and lists the problems that are due or
overdue. Nothing is written.

**"Fill in the missing complexity analysis."**
Claude reads the sheet with `read_range` and finds rows where
`time_complexity` or `space_complexity` is blank. For each one it works out
the complexity of the standard solution, then calls
`update_cells(tab="Sheet1", slug=..., fields={"time_complexity": "O(n)", "space_complexity": "O(1)"}, action="edited")`.
The user sees a list of what was filled in, taken from the `after` state of
each call.

**"I just did two-sum."**
Claude reads the `two-sum` row, picks the next interval and review date, and
calls `update_cells` with `action="completed"` and the new `interval_days`
and `next_review`. From the returned before and after state it confirms
something like "Two Sum: interval 17 -> 19, next review 2026-11-02". The log
entry has already been written by the server.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

1. **Google Cloud.** Create a project, enable the Google Sheets API, and
   create a service account. Download its JSON key to `secrets/` (gitignored).
2. **Share the sheet.** Share the spreadsheet with the service account's email
   address as an Editor.
3. **Lay out the tabs.** Headers go in row 1 and data starts in row 2. Header
   order doesn't matter, but the names do (matching ignores case and
   surrounding spaces).

   `Sheet1`: `problem`, `slug`, `next_review`, `interval_days`,
   `time_complexity`, `space_complexity`

   `log`: `timestamp`, `slug`, `action`, `interval_before`, `interval_after`,
   `next_review`

   Dates are stored as `yyyy-mm-dd`. Set the sheet's locale and time zone
   (File > Settings) so dates display in that format.
4. **Configure.** Copy `.env.example` to `.env` and set `SHEET_ID` (the long
   ID in the sheet's URL) and `GOOGLE_CREDENTIALS_PATH` (the path to the key
   file).
5. **Install.**
   ```
   uv sync
   ```
6. **Register with Claude Code**, from the repo root:
   ```
   claude mcp add sheetcode -- uv run sheetcode-mcp
   ```
   To register it from another directory, use
   `uv run --directory /path/to/SheetCode sheetcode-mcp` as the command.

## Testing

The client is tested with pytest against an in-memory fake of the Sheets
service (`tests/fake_sheets.py`). The fake reproduces API quirks that were
confirmed against the real sheet:

- Trailing empty cells are dropped from returned rows.
- A fully empty range has no `values` key at all.
- Every value comes back as a string.
- Reading past the end of the data returns nothing rather than an error.

The tests cover row lookup after the sheet is re-sorted, before/after state,
log entries for each action, failed log writes, and rejected writes that must
leave the sheet untouched. To confirm the tests check what they claim, the
client was deliberately broken in specific ways and the tests were confirmed
to fail. The MCP tool layer in `server.py` has no automated tests of its own.

```
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy src
```

CI runs all four on every push and pull request. No credentials are needed.

## Roadmap

- OAuth installed-app flow so other people can install it without a service
  account.
- A custom MCP client built on the Anthropic API, with a scripted opening.
- A consistency auditor: deterministic checks on what Claude wrote, such as
  dates not advanced, intervals off the expected sequence, blank required
  fields, and gaps between rows.
- Header caching. Every call currently re-reads the header row, which adds
  round trips to the API.
- Study tools over the log history, such as spotting weak topics and
  planning sessions.
- Configurable interval growth strategies.

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Tommy Tang.
