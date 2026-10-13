import re
from datetime import datetime

from googleapiclient.discovery import Resource

from .auth import get_service, load_config

LOG_TAB = "log"


def _column_letter(index: int) -> str:
    """Converts a 0-based column index to its A1 letter (0 -> 'A', 26 -> 'AA')."""
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _row_number_from_range(a1_range: str) -> int:
    match = re.search(r"![A-Z]+(\d+)", a1_range)
    if not match:
        raise RuntimeError(f"Could not parse a row number out of range {a1_range!r}")
    return int(match.group(1))


def _expected_row_count(range_str: str) -> int | None:
    """Returns how many rows an A1 range spans, or None if that can't be determined
    (e.g. an open-ended range like 'Sheet1!A:A')."""

    if "!" in range_str:
        range_str = range_str.split("!", 1)[1]
    if ":" not in range_str:
        return 1

    start, end = range_str.split(":", 1)
    start_match = re.search(r"\d+", start)
    end_match = re.search(r"\d+", end)
    if not start_match or not end_match:
        return None
    return int(end_match.group()) - int(start_match.group()) + 1


class SheetsClient:
    """All Google Sheets I/O for the tracked spreadsheet. Knows nothing about MCP."""

    def __init__(self, service: Resource, sheet_id: str) -> None:
        self.service = service
        self.sheet_id = sheet_id

    @classmethod
    def from_env(cls) -> "SheetsClient":
        """Builds a client from .env config, without exposing auth details to callers."""
        config = load_config()
        service = get_service(config)
        return cls(service=service, sheet_id=config.sheet_id)

    @property
    def _values(self):
        return self.service.spreadsheets().values()

    def _get_values(self, range_str: str) -> list[list[str]]:
        res = self._values.get(spreadsheetId=self.sheet_id, range=range_str).execute()
        return res.get("values", [])

    def _headers(self, tab: str) -> list[str]:
        rows = self._get_values(f"{tab}!1:1")
        if not rows:
            raise RuntimeError(f"Tab {tab!r} has no header row.")
        return [h.strip() for h in rows[0]]

    def _field_to_column(self, headers: list[str], field: str) -> int:
        lookup = {h.strip().lower(): i for i, h in enumerate(headers)}
        key = field.strip().lower()
        if key not in lookup:
            raise ValueError(
                f"Unknown field {field!r}; known fields are {list(lookup)}"
            )
        return lookup[key]

    def _row_as_dict(self, headers: list[str], range_str: str) -> dict[str, str]:
        rows = self._get_values(range_str)
        row = rows[0] if rows else []
        padded = row + [""] * (len(headers) - len(row))
        return dict(zip(headers, padded))

    def _find_row_by_slug(self, tab: str, slug: str) -> int | None:
        headers = self._headers(tab)
        slug_col = self._field_to_column(headers, "slug")
        letter = _column_letter(slug_col)
        column_values = self._get_values(f"{tab}!{letter}2:{letter}")
        for i, row in enumerate(column_values):
            value = row[0] if row else ""
            if value.strip() == slug:
                return i + 2
        return None

    def _append_log(
        self,
        slug: str,
        action: str,
        interval_before: str,
        interval_after: str,
        next_review: str,
    ) -> None:
        headers = self._headers(LOG_TAB)
        fields = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "slug": slug,
            "action": action,
            "interval_before": interval_before,
            "interval_after": interval_after,
            "next_review": next_review,
        }
        row = [""] * len(headers)
        for field, value in fields.items():
            row[self._field_to_column(headers, field)] = str(value)

        self._values.append(
            spreadsheetId=self.sheet_id,
            range=f"{LOG_TAB}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    def _log_or_warn(self, **entry: str) -> str | None:
        """Writes a log entry for a sheet write that has already happened. A
        failure here must not hide that write, so it comes back as a warning
        message instead of raising. Returns None when the entry was logged."""
        try:
            self._append_log(**entry)
        except Exception as exc:  # noqa: BLE001 - whatever broke, the write stands
            return (
                "The write succeeded, but its entry could not be added to the "
                f"{LOG_TAB!r} tab: {exc}"
            )
        return None

    def get_schema(self) -> dict:
        """Returns every tab's name, its headers mapped to column letters, and the
        first data row as a sample of the real value formats."""

        metadata = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.sheet_id, fields="sheets.properties.title")
            .execute()
        )
        tab_names = [s["properties"]["title"] for s in metadata.get("sheets", [])]

        ranges = [f"{tab}!1:2" for tab in tab_names]
        res = self._values.batchGet(
            spreadsheetId=self.sheet_id, ranges=ranges
        ).execute()
        value_ranges = res.get("valueRanges", [])

        tabs = {}
        for tab, value_range in zip(tab_names, value_ranges):
            rows = value_range.get("values", [])
            headers = [h.strip() for h in rows[0]] if rows else []
            sample = rows[1] if len(rows) > 1 else []
            padded_sample = sample + [""] * (len(headers) - len(sample))

            tabs[tab] = {
                "headers": {h: _column_letter(i) for i, h in enumerate(headers)},
                "sample_row": dict(zip(headers, padded_sample)) if headers else None,
            }

        return {"tabs": tabs}

    def read_range(self, range_str: str) -> dict:
        """Reads an A1 range. Includes a note when the range is empty or extends
        past the sheet's actual data, so the caller can self-correct."""

        values = self._get_values(range_str)
        note = None

        if not values:
            note = (
                f"No data found for range {range_str!r}. It may be empty or "
                "past the end of the sheet's data."
            )
        else:
            expected_rows = _expected_row_count(range_str)
            if expected_rows is not None and len(values) < expected_rows:
                note = (
                    f"Requested {expected_rows} row(s) but only {len(values)} were "
                    "returned; the range extends past the end of the data."
                )

        return {"values": values, "note": note}

    def append_row(self, tab: str, values: dict[str, str]) -> dict:
        """Appends a new row to tab, at a position the server chooses. values maps
        header names to cell values; unspecified headers are left blank. If the
        row is written but logging it fails, the result carries a "warning".
        The log tab is written only by the client itself, so appending to it
        raises ValueError."""

        if tab.strip().lower() == LOG_TAB:
            raise ValueError(
                f"Cannot append to the {LOG_TAB!r} tab; its entries are written "
                "automatically when a row is added or updated."
            )

        headers = self._headers(tab)
        row = [""] * len(headers)
        for field, value in values.items():
            row[self._field_to_column(headers, field)] = str(value)

        response = self._values.append(
            spreadsheetId=self.sheet_id,
            range=f"{tab}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        row_number = _row_number_from_range(response["updates"]["updatedRange"])

        warning = self._log_or_warn(
            slug=str(values.get("slug", "")),
            action="added",
            interval_before="",
            interval_after=str(values.get("interval_days", "")),
            next_review=str(values.get("next_review", "")),
        )

        result = {"tab": tab, "row": row_number, "values": dict(zip(headers, row))}
        if warning:
            result["warning"] = warning
        return result

    def update_cells(
        self, tab: str, slug: str, fields: dict[str, str], action: str
    ) -> dict:
        """Updates named fields on the row matching slug. Looks the row up by slug
        at write time, since the sheet may have been re-sorted by hand. action is
        recorded in the log entry: "completed", "skipped" or "edited". Returns the
        row's state before and after the write, read back from the sheet. If the
        cells are written but logging it fails, the result carries a "warning".
        The log tab is append-only, so updating it raises ValueError."""

        if tab.strip().lower() == LOG_TAB:
            raise ValueError(
                f"Cannot update the {LOG_TAB!r} tab; it is append-only and its "
                "entries are written automatically."
            )

        headers = self._headers(tab)
        row_number = self._find_row_by_slug(tab, slug)
        if row_number is None:
            raise ValueError(f"No row found in tab {tab!r} with slug {slug!r}")

        last_col = _column_letter(len(headers) - 1)
        row_range = f"{tab}!A{row_number}:{last_col}{row_number}"

        before = self._row_as_dict(headers, row_range)

        data = [
            {
                "range": f"{tab}!{_column_letter(self._field_to_column(headers, field))}{row_number}",
                "values": [[str(value)]],
            }
            for field, value in fields.items()
        ]
        self._values.batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

        after = self._row_as_dict(headers, row_range)

        warning = self._log_or_warn(
            slug=slug,
            action=action,
            interval_before=before.get("interval_days", ""),
            interval_after=after.get("interval_days", ""),
            next_review=after.get("next_review", ""),
        )

        result = {"slug": slug, "before": before, "after": after}
        if warning:
            result["warning"] = warning
        return result
