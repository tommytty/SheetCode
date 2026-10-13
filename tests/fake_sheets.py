"""An in-memory stand-in for the Google Sheets API service.

Implements only the calls SheetsClient makes, and reproduces the response
quirks listed in CLAUDE.md so the client is tested against them:

- trailing empty cells are truncated from every returned row
- a range with no data omits the "values" key entirely
- reading past the end of the data is not an error
- every value comes back as a string

Tests seed and inspect `FakeSheetsService.tabs` directly. Reordering those
rows is the equivalent of sorting the sheet by hand in the browser.
"""

import re
from collections.abc import Callable
from typing import Any

import httplib2
from googleapiclient.errors import HttpError

_A1 = re.compile(r"^([A-Z]*)(\d*)(?::([A-Z]*)(\d*))?$")


def _http_error(status: int, message: str) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), message.encode())


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return index - 1


def _column_letter(index: int) -> str:
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


class _Request:
    """Mirrors the real client: building a request does nothing until execute()."""

    def __init__(self, run: Callable[[], dict[str, Any]]) -> None:
        self._run = run

    def execute(self) -> dict[str, Any]:
        return self._run()


class FakeSheetsService:
    """One grid of strings per tab. Row 0 of each grid is the header row."""

    def __init__(self, tabs: dict[str, list[list[str]]]) -> None:
        self.tabs = {name: [list(row) for row in rows] for name, rows in tabs.items()}
        # Tab names whose appends fail with a 503, to simulate a write that
        # lands in one tab followed by a failed write to another.
        self.fail_appends_to: set[str] = set()

    def spreadsheets(self) -> "_Spreadsheets":
        return _Spreadsheets(self)

    def _parse(self, range_str: str) -> tuple[str, int, int, int | None, int | None]:
        """Splits an A1 range into (tab, first_row, first_col, last_row, last_col),
        0-based and inclusive. An open end, as in 'B2:B' or '1:1', is None."""
        if "!" in range_str:
            tab, cells = range_str.split("!", 1)
        else:
            tab, cells = next(iter(self.tabs)), range_str

        match = _A1.match(cells)
        if tab not in self.tabs or not match:
            raise _http_error(400, f"Unable to parse range: {range_str}")

        col0, row0, col1, row1 = match.groups()
        first_row = int(row0) - 1 if row0 else 0
        first_col = _column_index(col0) if col0 else 0
        if col1 is None:  # no colon: a single cell
            return tab, first_row, first_col, first_row, first_col
        last_row = int(row1) - 1 if row1 else None
        last_col = _column_index(col1) if col1 else None
        return tab, first_row, first_col, last_row, last_col

    def _read(self, range_str: str) -> dict[str, Any]:
        tab, first_row, first_col, last_row, last_col = self._parse(range_str)
        grid = self.tabs[tab]
        stop_row = len(grid) if last_row is None else min(last_row + 1, len(grid))
        stop_col = None if last_col is None else last_col + 1

        rows = []
        for grid_row in grid[first_row:stop_row]:
            row = list(grid_row[first_col:stop_col])
            while row and row[-1] == "":
                row.pop()
            rows.append(row)
        # Blank rows in the middle come back as [], blank rows at the end don't.
        while rows and not rows[-1]:
            rows.pop()

        value_range: dict[str, Any] = {"range": range_str, "majorDimension": "ROWS"}
        if rows:
            value_range["values"] = rows
        return value_range

    def _write(self, tab: str, row: int, col: int, values: list[list[Any]]) -> int:
        """Writes a block with its top-left corner at (row, col), growing the
        grid as needed. Returns the number of cells written."""
        grid = self.tabs[tab]
        written = 0
        for row_offset, row_values in enumerate(values):
            while len(grid) <= row + row_offset:
                grid.append([])
            target = grid[row + row_offset]
            for col_offset, value in enumerate(row_values):
                while len(target) <= col + col_offset:
                    target.append("")
                target[col + col_offset] = str(value)
                written += 1
        return written

    def _append(self, range_str: str, body: dict[str, Any]) -> dict[str, Any]:
        tab, _, first_col, _, _ = self._parse(range_str)
        if tab in self.fail_appends_to:
            raise _http_error(503, f"The service is currently unavailable ({tab}).")

        # The real API appends below the table it detects at `range`. Every tab
        # here is one contiguous table, so that is the row after the last one
        # holding any value.
        grid = self.tabs[tab]
        row = max(
            (i + 1 for i, r in enumerate(grid) if any(c != "" for c in r)), default=0
        )
        values = body["values"]
        written = self._write(tab, row, first_col, values)

        last_col = first_col + max(len(r) for r in values) - 1
        updated_range = (
            f"{tab}!{_column_letter(first_col)}{row + 1}"
            f":{_column_letter(last_col)}{row + len(values)}"
        )
        return {
            "updates": {
                "updatedRange": updated_range,
                "updatedRows": len(values),
                "updatedCells": written,
            }
        }

    def _batch_update(self, body: dict[str, Any]) -> dict[str, Any]:
        # Parse every range before writing any, so a bad one rejects the batch.
        targets = [
            (self._parse(item["range"]), item["values"]) for item in body["data"]
        ]
        written = 0
        for (tab, row, col, _, _), values in targets:
            written += self._write(tab, row, col, values)
        return {"totalUpdatedCells": written}


class _Spreadsheets:
    def __init__(self, fake: FakeSheetsService) -> None:
        self._fake = fake

    def get(self, spreadsheetId: str, fields: str | None = None) -> _Request:
        return _Request(
            lambda: {
                "sheets": [{"properties": {"title": name}} for name in self._fake.tabs]
            }
        )

    def values(self) -> "_Values":
        return _Values(self._fake)


class _Values:
    def __init__(self, fake: FakeSheetsService) -> None:
        self._fake = fake

    def get(self, spreadsheetId: str, range: str) -> _Request:
        return _Request(lambda: self._fake._read(range))

    def batchGet(self, spreadsheetId: str, ranges: list[str]) -> _Request:
        return _Request(lambda: {"valueRanges": [self._fake._read(r) for r in ranges]})

    def append(
        self,
        spreadsheetId: str,
        range: str,
        valueInputOption: str,
        insertDataOption: str,
        body: dict[str, Any],
    ) -> _Request:
        return _Request(lambda: self._fake._append(range, body))

    def batchUpdate(self, spreadsheetId: str, body: dict[str, Any]) -> _Request:
        return _Request(lambda: self._fake._batch_update(body))
