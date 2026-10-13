# SheetCode

An MCP server that connects Claude to a Google Sheet tracking LeetCode problems
and their spaced-repetition schedule.

## Core design principle

**Claude does all the reasoning. The server does none of it.**

The server has no scheduling logic: no prime number sequence, no date
arithmetic, no interval calculation. Claude reads the current state, decides
what the new values should be, and passes them in. The server's only job is to
read and write cells.

Do not add a "next prime" function, a date-math helper, or anything that
computes what a value *should* be. If such logic appears in this codebase, it
is a bug.

The server does own two things, because they are mechanical rather than
judgment calls:

- **Row identity.** Finding which row holds a given slug.
- **Append position.** Choosing where a new row goes.

These are removed from Claude because they are off-by-one hazards with no
upside, not because Claude cannot reason about them.

## Stack

- Python 3.12, managed by `uv`
- `mcp` SDK **v2** — the server class is `MCPServer`, imported as
  `from mcp.server import MCPServer`. The v1 path `mcp.server.fastmcp` does
  **not** exist in v2. Do not use `FastMCP`.
- `@mcp.tool()` with parentheses. Input schemas are generated from type hints.
- `google-api-python-client` + `google-auth` for Sheets
- `python-dotenv` for config, `pydantic` for models
- `ruff` for format and lint

## Project layout

```
src/sheetcode/
├── sheets/
│   ├── auth.py      # config + credentials + service (done)
│   └── client.py    # SheetsClient class — all Sheets I/O
├── mcp_server/
│   └── server.py    # MCP tools, thin wrappers over client
└── cli/             # phase 2
```

`sheets/` knows nothing about MCP. `mcp_server/` knows nothing about Google
auth. Keep that boundary.

## The spreadsheet

Tab `Sheet1`, headers in row 1, data from row 2:

| Column | Header |
|--------|--------|
| A | `problem` |
| B | `slug` |
| C | `next_review` |
| D | `interval_days` |
| E | `time_complexity` |
| F | `space_complexity` |

Tab `log`, append-only:

| Column | Header |
|--------|--------|
| A | `timestamp` |
| B | `slug` |
| C | `action` |
| D | `interval_before` |
| E | `interval_after` |
| F | `next_review` |

`action` is one of `added`, `completed`, `skipped`, `edited`.
`interval_before` is empty for `added` rows.

Dates are stored and displayed as `yyyy-mm-dd`. Sheet locale and timezone are
set to Canada / Vancouver.

### Row identity

Rows are identified by **slug**, never by position. The sheet gets sorted by
hand in the browser, so any cached row index is stale the moment that happens.
Every write looks up the row by slug at the time of the write.

Never hardcode a column index. Read the header row and build a
`header -> column` map, stripping and lowercasing each header. Addressing cells
as `row[2]` is wrong; `row[cols["next_review"]]` is right.

## Google Sheets API behaviour

These were confirmed against the real sheet. Code must handle them:

1. **Trailing empty cells are truncated.** Requesting `A7:G7` on a row whose
   column G is empty returns six values, not seven. Indexing past the end of a
   returned row raises `IndexError`. This will happen on rows where the
   complexity columns are blank.
2. **Fully empty ranges omit `values` entirely.** The response has no `values`
   key at all, not an empty list. Always use `res.get("values", [])`.
3. **Everything comes back as strings** under the default
   `FORMATTED_VALUE` render option — `'19'`, not `19`; `'2026-08-18'`, not a
   serial number. This is desirable here: Claude reads the ISO date directly
   and no epoch conversion is needed anywhere. Do not switch to
   `UNFORMATTED_VALUE`.
4. **Reading past the end of the data is not an error.** It returns an empty
   result. Writing past the end is also legal and silently grows the sheet,
   which is why Claude does not choose append positions.

## Tools

Four tools. Each is a thin wrapper over `SheetsClient`; logic lives in the
client, not in the tool function.

### `get_schema()`

Returns tab names, headers, and their column letters, plus the first data row
as a sample so Claude can see the actual value formats.

Does **not** return a row count. Nothing consumes it: `append_row` computes its
own target and `update_cells` looks up by slug. A count would only go stale.

The description should tell Claude to call this before reading or writing.
Other tool descriptions should cross-reference it.

### `read_range(range_str)`

Returns the values for an A1 range, plus a note when the range is empty or
extends past the data, so Claude can self-correct rather than seeing a bare
empty result.

### `append_row(tab, values)`

Adds a row at the end. The server finds the end of the data; Claude never
passes a row number. Used for new problems only, not for log entries: the
client writes those itself, and `append_row` raises `ValueError` when `tab` is
the `log` tab.

### `update_cells(tab, slug, fields, action)`

Updates named fields on the row matching `slug`. Claude passes field names and
values, never an A1 range, so it cannot write to the wrong row.

`action` controls the `log` tab entry for this write and is required, with no
default: `"completed"` or `"skipped"` for a review, `"edited"` for a change
that is not a review, such as filling in the complexity columns. Claude always
says which, since the server has no basis for inferring intent from which
fields changed.

Returns **before and after state**:

```python
{"slug": ..., "before": {...}, "after": {...}}
```

Read the row first, write, then read back. Two reasons: `after` is what is
actually in the sheet rather than what was requested, which is read-back
verification; and `before` is what lets Claude say "interval 17 -> 19" in its
confirmation. Without capturing `before` ahead of the write, that information
is gone.

## Logging

`append_row` and `update_cells` write to the `log` tab automatically, inside
the tool. Claude is never asked to log as a separate step, because it would
sometimes forget, and because a log the model writes records what it *says* it
did rather than what happened.

## stdio transport

The server runs over stdio. **stdout is the JSON-RPC channel.** Never `print()`
to stdout — it corrupts the protocol and drops the connection. Debug output
goes to stderr.

The server cannot write to the user's terminal. Confirmation messages like
"interval 17 -> 19" are written by Claude from tool return values, which is why
those return values must carry everything needed to compose them.

## Conventions

- Type hints on arguments and returns; `mypy` runs in CI
- Docstrings are the **first statement inside** a function. A string above the
  `def` is not a docstring and MCPServer will not see it. Tool docstrings become
  the description sent to the model — they are interface, not commentary.
- Dependency injection: functions receive config, they do not reach out for it
- `_` prefix for module-internal helpers only
- Keep `mcp` pinned `>=2.0.0,<3.0.0`

## Secrets

`.env` and `secrets/` are gitignored and must stay that way. `.env.example` is
committed with placeholder values. The service account JSON contains a real
private key. Never read it into a tool response or a log line.

## Current state

`auth.py` and `client.py` are complete. `client.py` is tested in
`tests/test_client.py` against an in-memory fake in `tests/fake_sheets.py`,
and the tests were verified to fail under deliberate mutations. `server.py`
has the four tools. They were exercised in-process against the fake but not
yet against the real sheet, and `server.py` has no automated tests of its own.

## Out of scope for v1

Deliberately deferred, listed in the README roadmap:

- OAuth installed-app flow for multi-user install (service account only for now)
- Custom MCP client using the Anthropic API, with a scripted opening
- `check_consistency()` auditor: deterministic code verifying Claude's output
  (dates not bumped, intervals not prime, blank required fields, row gaps)
- Weakness detection and study planning tools over the log history
- Configurable interval growth strategies
