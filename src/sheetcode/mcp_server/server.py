from typing import Literal

from mcp.server import MCPServer

from sheetcode.sheets.client import SheetsClient

mcp = MCPServer("SheetCode")

_client: SheetsClient | None = None


def _get_client() -> SheetsClient:
    """Builds the SheetsClient on the first tool call and reuses it after that.
    Nothing is read from .env at import, so the server starts and lists its
    tools even when the config is missing; the tool call is what fails."""
    global _client
    if _client is None:
        _client = SheetsClient.from_env()
    return _client


@mcp.tool()
def get_schema() -> dict:
    """Describes the spreadsheet: every tab, its column headers with their
    column letters, and the first data row of each tab as a sample of the real
    value formats.

    Call this first, before any other SheetCode tool, and again if a tool
    reports an unknown field. The header names it returns are the field names
    that append_row and update_cells accept, and the column letters are what
    you need to build ranges for read_range.

    Returns {"tabs": {tab_name: {"headers": {header: column_letter},
    "sample_row": {header: value}}}}. Headers are in row 1 and data starts at
    row 2. sample_row holds blank strings when the tab has no data yet.

    There is no row count. To see the data, call read_range with an open-ended
    range such as "Sheet1!A2:F".
    """
    return _get_client().get_schema()


@mcp.tool()
def read_range(range_str: str) -> dict:
    """Reads cells from the spreadsheet. Changes nothing.

    range_str is an A1 range that includes the tab name, such as
    "Sheet1!A2:F20", or "Sheet1!A2:F" to read every row without knowing how
    many there are. Call get_schema first for the tab names and column letters.

    Returns {"values": [[...], ...], "note": ...}. Every value is a string,
    exactly as the sheet displays it: dates as yyyy-mm-dd, numbers as digits.
    Trailing blank cells are dropped from each row, so a row can be shorter
    than the range is wide. Match values to columns by position from the
    range's first column and treat missing cells as blank.

    note is null on a normal read. When nothing comes back, or fewer rows than
    the range asked for, note says why, so you can tell an empty sheet from a
    range that points past the end of the data.

    Rows are re-sorted by hand, so a row number seen here is not a stable way
    to identify a problem. Identify problems by slug.
    """
    return _get_client().read_range(range_str)


@mcp.tool()
def append_row(tab: str, values: dict[str, str]) -> dict:
    """Adds one new row at the end of a tab. Use it to start tracking a new
    problem.

    The server chooses the row. Do not look for an empty row and do not pass a
    row number; there is no parameter for one.

    values maps header names from get_schema to cell values, for example
    {"problem": "Two Sum", "slug": "two-sum", "next_review": "2026-10-20",
    "interval_days": "2"}. Headers you leave out stay blank. A name that is
    not a header in that tab is an error, and nothing is written. Pass every
    value as a string in the format the get_schema sample row shows. The
    server calculates nothing: you decide the date and the interval.

    Duplicates are not checked. Before adding a problem, read the slug column
    with read_range to confirm it is not already tracked.

    An "added" entry is written to the log tab automatically. Never append to
    the log tab yourself; doing so is an error.

    Returns {"tab", "row", "values"}: the row number the server chose and the
    full row as written. If the result also has a "warning", the row was added
    but its log entry was not. Tell the user, and do not retry, since that
    would add the row twice.
    """
    return _get_client().append_row(tab, values)


@mcp.tool()
def update_cells(
    tab: str,
    slug: str,
    fields: dict[str, str],
    action: Literal["completed", "skipped", "edited"],
) -> dict:
    """Changes named fields on the one row whose slug matches. Use it after
    the user completes or skips a review, or to edit a problem's other details.

    It takes field names, not A1 ranges. fields maps header names from
    get_schema to their new values, for example {"next_review": "2026-11-01",
    "interval_days": "11"}. The server finds the row by slug at the moment it
    writes, so never pass or rely on a row number. Fields you do not name are
    left as they are. An unknown slug or an unknown field name is an error,
    and nothing is written.

    The server does no scheduling. Read the row's current values with
    read_range, work out the new ones yourself, and pass the final values as
    strings.

    action is required. It says what kind of change this is and is recorded in
    the log tab, which is written automatically. The server cannot tell the
    three apart from the fields, so you must say which it was:

    - "completed": the user did the review and you are rescheduling it.
    - "skipped": the user chose not to do a review that was due and you are
      only rescheduling it.
    - "edited": the change is not a review at all, for example filling in
      time_complexity and space_complexity on a problem that had them blank.

    Returns {"slug", "before", "after"}, where before and after are the full
    row keyed by header, and after is read back from the sheet once the write
    is done. Use them to confirm the change to the user, for example
    "interval 7 -> 11, next review 2026-11-01". If the result also has a
    "warning", the cells were updated but the log entry was not. Tell the
    user, and do not retry.
    """
    return _get_client().update_cells(tab, slug, fields, action)


def main() -> None:
    """Entry point for the sheetcode-mcp script. Serves over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
