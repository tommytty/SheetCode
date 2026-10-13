"""Tests for SheetsClient, run against the in-memory fake in fake_sheets.py.

Assertions are on what ends up in the sheet and on what the client returns,
never on which API methods were called.
"""

import copy
from datetime import datetime

import pytest
from fake_sheets import FakeSheetsService

from sheetcode.sheets.client import SheetsClient

SHEET_HEADERS = [
    "problem",
    "slug",
    "next_review",
    "interval_days",
    "time_complexity",
    "space_complexity",
]
LOG_HEADERS = [
    "timestamp",
    "slug",
    "action",
    "interval_before",
    "interval_after",
    "next_review",
]

# The complexity columns are blank on this row, so the fake truncates it to
# four values on every read, as the real API does.
TWO_SUM = ["Two Sum", "two-sum", "2026-10-20", "7"]
LRU_CACHE = ["LRU Cache", "lru-cache", "2026-10-15", "3", "O(1)", "O(n)"]
VALID_PARENS = [
    "Valid Parentheses",
    "valid-parentheses",
    "2026-11-01",
    "19",
    "O(n)",
    "O(n)",
]

SEEDED_LOG_ROW = [
    "2026-10-01T09:00:00-07:00",
    "two-sum",
    "added",
    "",
    "7",
    "2026-10-20",
]


@pytest.fixture
def fake() -> FakeSheetsService:
    return FakeSheetsService(
        {
            "Sheet1": [SHEET_HEADERS, TWO_SUM, LRU_CACHE, VALID_PARENS],
            "log": [LOG_HEADERS, SEEDED_LOG_ROW],
        }
    )


@pytest.fixture
def client(fake: FakeSheetsService) -> SheetsClient:
    return SheetsClient(service=fake, sheet_id="fake-sheet-id")


def sheet_row(fake: FakeSheetsService, slug: str) -> dict[str, str]:
    """The Sheet1 row holding slug, wherever it currently sits, as a dict with
    blank trailing cells filled in."""
    matches = [row for row in fake.tabs["Sheet1"][1:] if row[1:2] == [slug]]
    assert len(matches) == 1, f"expected exactly one row for {slug!r}, got {matches}"
    row = matches[0]
    return dict(zip(SHEET_HEADERS, row + [""] * (len(SHEET_HEADERS) - len(row))))


def log_entries(fake: FakeSheetsService) -> list[dict[str, str]]:
    return [dict(zip(LOG_HEADERS, row)) for row in fake.tabs["log"][1:]]


def new_log_entry(fake: FakeSheetsService) -> dict[str, str]:
    """The single entry a write should have added after the seeded one."""
    entries = log_entries(fake)
    assert len(entries) == 2, f"expected one new log entry, log is {entries}"
    assert entries[0] == dict(zip(LOG_HEADERS, SEEDED_LOG_ROW))
    # Raises if the timestamp is not ISO 8601; it must also carry a UTC offset.
    assert datetime.fromisoformat(entries[1]["timestamp"]).tzinfo is not None
    return entries[1]


# get_schema


def test_get_schema_maps_headers_to_column_letters_on_both_tabs(
    client: SheetsClient,
) -> None:
    tabs = client.get_schema()["tabs"]

    assert list(tabs) == ["Sheet1", "log"]
    assert tabs["Sheet1"]["headers"] == {
        "problem": "A",
        "slug": "B",
        "next_review": "C",
        "interval_days": "D",
        "time_complexity": "E",
        "space_complexity": "F",
    }
    assert tabs["log"]["headers"] == {
        "timestamp": "A",
        "slug": "B",
        "action": "C",
        "interval_before": "D",
        "interval_after": "E",
        "next_review": "F",
    }


def test_get_schema_pads_a_sample_row_with_blank_trailing_cells(
    client: SheetsClient,
) -> None:
    schema = client.get_schema()

    assert schema["tabs"]["Sheet1"]["sample_row"] == {
        "problem": "Two Sum",
        "slug": "two-sum",
        "next_review": "2026-10-20",
        "interval_days": "7",
        "time_complexity": "",
        "space_complexity": "",
    }


def test_get_schema_samples_the_first_log_row(client: SheetsClient) -> None:
    schema = client.get_schema()

    assert schema["tabs"]["log"]["sample_row"] == {
        "timestamp": "2026-10-01T09:00:00-07:00",
        "slug": "two-sum",
        "action": "added",
        "interval_before": "",
        "interval_after": "7",
        "next_review": "2026-10-20",
    }


def test_get_schema_on_a_tab_with_headers_only_gives_a_blank_sample(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    del fake.tabs["log"][1:]

    log = client.get_schema()["tabs"]["log"]

    assert list(log["headers"]) == LOG_HEADERS
    assert log["sample_row"] == dict.fromkeys(LOG_HEADERS, "")


def test_get_schema_does_not_report_a_row_count(client: SheetsClient) -> None:
    schema = client.get_schema()

    assert set(schema) == {"tabs"}
    for tab in schema["tabs"].values():
        assert set(tab) == {"headers", "sample_row"}


# read_range


def test_read_range_returns_rows_as_the_api_gives_them(client: SheetsClient) -> None:
    result = client.read_range("Sheet1!A2:F3")

    # The first row comes back four cells wide: its blank E and F are truncated.
    assert result == {"values": [TWO_SUM, LRU_CACHE], "note": None}


def test_read_range_past_the_data_returns_nothing_with_a_note(
    client: SheetsClient,
) -> None:
    result = client.read_range("Sheet1!A50:F60")

    assert result["values"] == []
    assert "Sheet1!A50:F60" in result["note"]


def test_read_range_of_blank_cells_inside_the_data_returns_nothing_with_a_note(
    client: SheetsClient,
) -> None:
    result = client.read_range("Sheet1!E2:F2")

    assert result["values"] == []
    assert result["note"]


def test_read_range_extending_past_the_data_returns_what_exists_with_a_note(
    client: SheetsClient,
) -> None:
    result = client.read_range("Sheet1!A3:F9")

    assert result["values"] == [LRU_CACHE, VALID_PARENS]
    assert "Requested 7 row(s) but only 2" in result["note"]


def test_read_range_open_ended_gets_no_note(client: SheetsClient) -> None:
    result = client.read_range("Sheet1!B2:B")

    assert result == {
        "values": [["two-sum"], ["lru-cache"], ["valid-parentheses"]],
        "note": None,
    }


# append_row


def test_append_row_lands_after_the_last_row(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    result = client.append_row(
        "Sheet1",
        {
            "problem": "3Sum",
            "slug": "3sum",
            "next_review": "2026-10-14",
            "interval_days": "2",
        },
    )

    assert fake.tabs["Sheet1"] == [
        SHEET_HEADERS,
        TWO_SUM,
        LRU_CACHE,
        VALID_PARENS,
        ["3Sum", "3sum", "2026-10-14", "2", "", ""],
    ]
    assert result == {
        "tab": "Sheet1",
        "row": 5,
        "values": {
            "problem": "3Sum",
            "slug": "3sum",
            "next_review": "2026-10-14",
            "interval_days": "2",
            "time_complexity": "",
            "space_complexity": "",
        },
    }


def test_append_row_places_values_by_header_name_not_argument_order(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    client.append_row(
        "Sheet1",
        {
            "space_complexity": "O(1)",
            "interval_days": "2",
            "slug": "3sum",
            "problem": "3Sum",
        },
    )

    assert fake.tabs["Sheet1"][-1] == ["3Sum", "3sum", "", "2", "", "O(1)"]


def test_append_row_logs_an_added_entry(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    client.append_row(
        "Sheet1",
        {
            "problem": "3Sum",
            "slug": "3sum",
            "next_review": "2026-10-14",
            "interval_days": "2",
        },
    )

    entry = new_log_entry(fake)
    del entry["timestamp"]
    assert entry == {
        "slug": "3sum",
        "action": "added",
        "interval_before": "",
        "interval_after": "2",
        "next_review": "2026-10-14",
    }


def test_append_row_with_an_unknown_field_raises_and_writes_nothing(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    tabs_before = copy.deepcopy(fake.tabs)

    with pytest.raises(ValueError, match="Unknown field 'difficulty'"):
        client.append_row("Sheet1", {"slug": "3sum", "difficulty": "medium"})

    assert fake.tabs == tabs_before


# update_cells


def test_update_cells_finds_the_row_after_the_sheet_is_reordered(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    # Sorted by next_review in the browser: two-sum moves from row 2 to row 3.
    fake.tabs["Sheet1"][1:] = [list(LRU_CACHE), list(TWO_SUM), list(VALID_PARENS)]

    client.update_cells(
        "Sheet1", "two-sum", {"interval_days": "11", "next_review": "2026-10-31"}
    )

    assert fake.tabs["Sheet1"] == [
        SHEET_HEADERS,
        LRU_CACHE,
        ["Two Sum", "two-sum", "2026-10-31", "11"],
        VALID_PARENS,
    ]


def test_update_cells_returns_before_and_after_state(client: SheetsClient) -> None:
    result = client.update_cells(
        "Sheet1", "two-sum", {"interval_days": "11", "next_review": "2026-10-31"}
    )

    assert result == {
        "slug": "two-sum",
        "before": {
            "problem": "Two Sum",
            "slug": "two-sum",
            "next_review": "2026-10-20",
            "interval_days": "7",
            "time_complexity": "",
            "space_complexity": "",
        },
        "after": {
            "problem": "Two Sum",
            "slug": "two-sum",
            "next_review": "2026-10-31",
            "interval_days": "11",
            "time_complexity": "",
            "space_complexity": "",
        },
    }


def test_update_cells_after_state_is_read_back_from_the_sheet(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    result = client.update_cells(
        "Sheet1", "lru-cache", {"interval_days": "5", "time_complexity": "O(log n)"}
    )

    assert result["after"] == sheet_row(fake, "lru-cache")
    assert result["after"]["space_complexity"] == "O(n)"


def test_update_cells_logs_a_completed_entry_with_both_intervals(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    fake.tabs["Sheet1"][1:] = [list(VALID_PARENS), list(LRU_CACHE), list(TWO_SUM)]

    client.update_cells(
        "Sheet1", "two-sum", {"interval_days": "11", "next_review": "2026-10-31"}
    )

    entry = new_log_entry(fake)
    del entry["timestamp"]
    assert entry == {
        "slug": "two-sum",
        "action": "completed",
        "interval_before": "7",
        "interval_after": "11",
        "next_review": "2026-10-31",
    }


def test_update_cells_skipped_logs_a_skipped_entry(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    result = client.update_cells(
        "Sheet1", "lru-cache", {"next_review": "2026-10-16"}, action="skipped"
    )

    assert sheet_row(fake, "lru-cache")["next_review"] == "2026-10-16"
    assert result["before"]["next_review"] == "2026-10-15"
    assert result["after"]["next_review"] == "2026-10-16"

    entry = new_log_entry(fake)
    del entry["timestamp"]
    assert entry == {
        "slug": "lru-cache",
        "action": "skipped",
        "interval_before": "3",
        "interval_after": "3",
        "next_review": "2026-10-16",
    }


def test_update_cells_on_an_unknown_slug_raises_and_writes_nothing(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    tabs_before = copy.deepcopy(fake.tabs)

    with pytest.raises(ValueError, match="No row found .* slug 'three-sum'"):
        client.update_cells("Sheet1", "three-sum", {"interval_days": "5"})

    assert fake.tabs == tabs_before


def test_update_cells_with_an_unknown_field_raises_and_writes_nothing(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    tabs_before = copy.deepcopy(fake.tabs)

    with pytest.raises(ValueError, match="Unknown field 'difficulty'"):
        client.update_cells(
            "Sheet1", "two-sum", {"interval_days": "11", "difficulty": "easy"}
        )

    assert fake.tabs == tabs_before


def test_update_cells_on_a_tab_with_no_data_rows_raises_for_the_missing_slug(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    # The slug column read comes back with no "values" key at all.
    del fake.tabs["Sheet1"][1:]
    tabs_before = copy.deepcopy(fake.tabs)

    with pytest.raises(ValueError, match="No row found .* slug 'two-sum'"):
        client.update_cells("Sheet1", "two-sum", {"interval_days": "11"})

    assert fake.tabs == tabs_before


# tabs with no header row


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda client: client.append_row("Sheet1", {"slug": "3sum"}),
            id="append_row",
        ),
        pytest.param(
            lambda client: client.update_cells(
                "Sheet1", "two-sum", {"interval_days": "11"}
            ),
            id="update_cells",
        ),
    ],
)
def test_writing_to_a_tab_with_no_header_row_raises_and_writes_nothing(
    fake: FakeSheetsService, client: SheetsClient, call
) -> None:
    fake.tabs["Sheet1"].clear()
    tabs_before = copy.deepcopy(fake.tabs)

    with pytest.raises(RuntimeError, match="'Sheet1' has no header row"):
        call(client)

    assert fake.tabs == tabs_before


# log failures after a successful write


def test_successful_writes_carry_no_warning(client: SheetsClient) -> None:
    appended = client.append_row("Sheet1", {"problem": "3Sum", "slug": "3sum"})
    updated = client.update_cells("Sheet1", "two-sum", {"interval_days": "11"})

    assert "warning" not in appended
    assert "warning" not in updated


def test_append_row_returns_a_warning_when_the_log_write_fails(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    fake.fail_appends_to.add("log")

    result = client.append_row(
        "Sheet1", {"problem": "3Sum", "slug": "3sum", "interval_days": "2"}
    )

    assert fake.tabs["Sheet1"][-1] == ["3Sum", "3sum", "", "2", "", ""]
    assert result["row"] == 5
    assert result["values"]["slug"] == "3sum"
    assert "log" in result["warning"]
    assert fake.tabs["log"] == [LOG_HEADERS, SEEDED_LOG_ROW]


def test_update_cells_returns_a_warning_when_the_log_write_fails(
    fake: FakeSheetsService, client: SheetsClient
) -> None:
    fake.fail_appends_to.add("log")

    result = client.update_cells(
        "Sheet1", "two-sum", {"interval_days": "11", "next_review": "2026-10-31"}
    )

    assert sheet_row(fake, "two-sum")["interval_days"] == "11"
    assert result["before"]["interval_days"] == "7"
    assert result["after"]["interval_days"] == "11"
    assert result["after"]["next_review"] == "2026-10-31"
    assert "log" in result["warning"]
    assert fake.tabs["log"] == [LOG_HEADERS, SEEDED_LOG_ROW]
