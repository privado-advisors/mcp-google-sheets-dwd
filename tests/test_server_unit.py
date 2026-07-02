import ast
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp_google_sheets import server


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingValuesResource:
    def __init__(self):
        self.calls = []
        self.get_results = {}
        self.update_result = {"updatedCells": 4}
        self.batch_update_result = {"totalUpdatedCells": 4}

    def get(self, **kwargs):
        self.calls.append(("values.get", kwargs))
        result = self.get_results.get(kwargs.get("range"), {"values": []})
        return FakeRequest(result)

    def update(self, **kwargs):
        self.calls.append(("values.update", kwargs))
        return FakeRequest(self.update_result)

    def batchUpdate(self, **kwargs):
        self.calls.append(("values.batchUpdate", kwargs))
        return FakeRequest(self.batch_update_result)


class RecordingSheetsSubresource:
    def __init__(self):
        self.calls = []
        self.copy_result = {"sheetId": 99, "title": "Copy of Sheet1"}

    def copyTo(self, **kwargs):
        self.calls.append(("sheets.copyTo", kwargs))
        return FakeRequest(self.copy_result)


class RecordingSpreadsheetsResource:
    def __init__(self):
        self.calls = []
        self.values_resource = RecordingValuesResource()
        self.sheets_resource = RecordingSheetsSubresource()
        self.metadata = {
            "properties": {"title": "Book"},
            "sheets": [
                {"properties": {"title": "Sheet1", "sheetId": 123}},
                {"properties": {"title": "Data", "sheetId": 456}},
            ],
        }
        self.batch_update_result = {"replies": [{"addChart": {"chart": {"chartId": 7}}}]}

    def get(self, **kwargs):
        self.calls.append(("spreadsheets.get", kwargs))
        return FakeRequest(self.metadata)

    def values(self):
        return self.values_resource

    def sheets(self):
        return self.sheets_resource

    def batchUpdate(self, **kwargs):
        self.calls.append(("spreadsheets.batchUpdate", kwargs))
        return FakeRequest(self.batch_update_result)


class RecordingSheetsService:
    def __init__(self):
        self.spreadsheets_resource = RecordingSpreadsheetsResource()

    def spreadsheets(self):
        return self.spreadsheets_resource


class RecordingFilesResource:
    def __init__(self):
        self.calls = []
        self.create_result = {
            "id": "spreadsheet-id",
            "name": "Created Sheet",
            "parents": ["folder-id"],
        }
        self.list_result = {
            "files": [
                {
                    "id": "one",
                    "name": "Budget 2026",
                    "createdTime": "2026-01-01T00:00:00Z",
                    "modifiedTime": "2026-01-02T00:00:00Z",
                    "owners": [{"emailAddress": "owner@example.com"}],
                    "webViewLink": "https://example.test/sheet",
                    "parents": ["folder-id"],
                }
            ]
        }

    def create(self, **kwargs):
        self.calls.append(("files.create", kwargs))
        return FakeRequest(self.create_result)

    def list(self, **kwargs):
        self.calls.append(("files.list", kwargs))
        return FakeRequest(self.list_result)


class RecordingDriveService:
    def __init__(self):
        self.files_resource = RecordingFilesResource()

    def files(self):
        return self.files_resource


def fake_ctx(sheets_service=None, drive_service=None, folder_id=None):
    lifespan_context = SimpleNamespace(
        sheets_service=sheets_service,
        drive_service=drive_service,
        folder_id=folder_id,
    )
    request_context = SimpleNamespace(lifespan_context=lifespan_context)
    return SimpleNamespace(request_context=request_context)


class ParseEnabledToolsTests(unittest.TestCase):
    def test_cli_include_tools_takes_precedence_over_environment(self):
        with patch.object(sys, "argv", ["mcp-google-sheets", "--include-tools", "a, b"]):
            with patch.dict(os.environ, {"ENABLED_TOOLS": "c"}, clear=False):
                self.assertEqual(server._parse_enabled_tools(), {"a", "b"})

    def test_empty_configuration_enables_all_tools(self):
        with patch.object(sys, "argv", ["mcp-google-sheets"]):
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(server._parse_enabled_tools())


class StdioSafetyTests(unittest.TestCase):
    def test_server_module_does_not_call_print(self):
        source_path = os.path.abspath(server.__file__)
        with open(source_path, "r", encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read(), filename=source_path)

        print_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]

        self.assertEqual(print_calls, [])

    def test_main_writes_no_diagnostics_to_stdout(self):
        with patch.object(server.mcp, "run") as run:
            with patch.object(server, "_configure_logging"):
                with patch.object(server.logger, "info"):
                    with patch.object(sys, "argv", ["mcp-google-sheets"]):
                        with redirect_stdout(StringIO()) as stdout:
                            server.main()

        self.assertEqual(stdout.getvalue(), "")
        run.assert_called_once_with(transport="stdio")

    def test_main_configures_logging(self):
        with patch.object(server.mcp, "run") as run:
            with patch.object(server, "_configure_logging") as configure_logging:
                with patch.object(server.logger, "info"):
                    with patch.object(sys, "argv", ["mcp-google-sheets"]):
                        server.main()

        configure_logging.assert_called_once_with()
        run.assert_called_once_with(transport="stdio")


class A1HelperTests(unittest.TestCase):
    def test_column_index_to_letter(self):
        self.assertEqual(server._column_index_to_letter(0), "A")
        self.assertEqual(server._column_index_to_letter(25), "Z")
        self.assertEqual(server._column_index_to_letter(26), "AA")
        self.assertEqual(server._column_index_to_letter(701), "ZZ")

    def test_parse_a1_range(self):
        self.assertEqual(
            server._parse_a1_notation("B2:D5"),
            {
                "startColumnIndex": 1,
                "startRowIndex": 1,
                "endColumnIndex": 4,
                "endRowIndex": 5,
            },
        )

    def test_parse_column_range(self):
        self.assertEqual(
            server._parse_a1_notation("A:C"),
            {"startColumnIndex": 0, "endColumnIndex": 3},
        )

    def test_parse_invalid_a1_range_raises(self):
        with self.assertRaises(ValueError):
            server._parse_a1_notation("not a range")

    def test_split_chart_source_ranges_splits_multi_column_table(self):
        source_range = {
            "sheetId": 123,
            "startRowIndex": 0,
            "endRowIndex": 3,
            "startColumnIndex": 0,
            "endColumnIndex": 3,
        }

        domain_range, series_ranges = server._split_chart_source_ranges(source_range)

        self.assertEqual(
            domain_range,
            {
                "sheetId": 123,
                "startRowIndex": 0,
                "endRowIndex": 3,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
        )
        self.assertEqual(
            series_ranges,
            [
                {
                    "sheetId": 123,
                    "startRowIndex": 0,
                    "endRowIndex": 3,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                {
                    "sheetId": 123,
                    "startRowIndex": 0,
                    "endRowIndex": 3,
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
            ],
        )


class ToolRequestConstructionTests(unittest.TestCase):
    def test_get_sheet_data_uses_values_api_by_default(self):
        sheets_service = RecordingSheetsService()
        values_resource = sheets_service.spreadsheets_resource.values_resource
        values_resource.get_results["Sheet1!A1:B2"] = {"values": [["a", "b"]]}

        result = server.get_sheet_data(
            "spreadsheet-id",
            "Sheet1",
            "A1:B2",
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(
            result,
            {
                "spreadsheetId": "spreadsheet-id",
                "valueRanges": [{"range": "Sheet1!A1:B2", "values": [["a", "b"]]}],
            },
        )
        self.assertEqual(
            values_resource.calls[-1],
            (
                "values.get",
                {"spreadsheetId": "spreadsheet-id", "range": "Sheet1!A1:B2"},
            ),
        )

    def test_update_cells_uses_user_entered_values(self):
        sheets_service = RecordingSheetsService()

        result = server.update_cells(
            "spreadsheet-id",
            "Sheet1",
            "A1:B2",
            [[1, 2], [3, 4]],
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(result, {"updatedCells": 4})
        self.assertEqual(
            sheets_service.spreadsheets_resource.values_resource.calls[-1],
            (
                "values.update",
                {
                    "spreadsheetId": "spreadsheet-id",
                    "range": "Sheet1!A1:B2",
                    "valueInputOption": "USER_ENTERED",
                    "body": {"values": [[1, 2], [3, 4]]},
                },
            ),
        )

    def test_batch_update_rejects_empty_requests_before_api_call(self):
        sheets_service = RecordingSheetsService()

        result = server.batch_update(
            "spreadsheet-id",
            [],
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(result, {"error": "requests list cannot be empty"})
        self.assertEqual(sheets_service.spreadsheets_resource.calls, [])

    def test_batch_update_sends_raw_requests(self):
        sheets_service = RecordingSheetsService()
        requests = [{"updateSheetProperties": {"fields": "title"}}]

        result = server.batch_update(
            "spreadsheet-id",
            requests,
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(result, {"replies": [{"addChart": {"chart": {"chartId": 7}}}]})
        self.assertEqual(
            sheets_service.spreadsheets_resource.calls[-1],
            (
                "spreadsheets.batchUpdate",
                {"spreadsheetId": "spreadsheet-id", "body": {"requests": requests}},
            ),
        )

    def test_add_rows_builds_insert_dimension_request(self):
        sheets_service = RecordingSheetsService()

        server.add_rows(
            "spreadsheet-id",
            "Sheet1",
            3,
            start_row=2,
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        _, call = sheets_service.spreadsheets_resource.calls[-1]
        self.assertEqual(call["spreadsheetId"], "spreadsheet-id")
        self.assertEqual(
            call["body"]["requests"][0]["insertDimension"],
            {
                "range": {
                    "sheetId": 123,
                    "dimension": "ROWS",
                    "startIndex": 2,
                    "endIndex": 5,
                },
                "inheritFromBefore": True,
            },
        )

    def test_add_rows_returns_error_for_missing_sheet(self):
        sheets_service = RecordingSheetsService()

        result = server.add_rows(
            "spreadsheet-id",
            "Missing",
            1,
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(result, {"error": "Sheet 'Missing' not found"})

    def test_create_spreadsheet_targets_requested_folder(self):
        drive_service = RecordingDriveService()

        with patch.object(server.logger, "info"):
            result = server.create_spreadsheet(
                "Created Sheet",
                folder_id="folder-id",
                ctx=fake_ctx(drive_service=drive_service),
            )

        self.assertEqual(
            result,
            {
                "spreadsheetId": "spreadsheet-id",
                "title": "Created Sheet",
                "folder": "folder-id",
            },
        )
        self.assertEqual(
            drive_service.files_resource.calls[-1],
            (
                "files.create",
                {
                    "supportsAllDrives": True,
                    "body": {
                        "name": "Created Sheet",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "parents": ["folder-id"],
                    },
                    "fields": "id, name, parents",
                },
            ),
        )

    def test_search_spreadsheets_clamps_page_size_and_returns_metadata(self):
        drive_service = RecordingDriveService()

        result = server.search_spreadsheets(
            "budget",
            max_results=500,
            ctx=fake_ctx(drive_service=drive_service),
        )

        self.assertEqual(result[0]["id"], "one")
        self.assertEqual(result[0]["owners"], ["owner@example.com"])
        _, call = drive_service.files_resource.calls[-1]
        self.assertEqual(call["pageSize"], 100)
        self.assertIn("name contains 'budget'", call["q"])
        self.assertIn("fullText contains 'budget'", call["q"])

    def test_find_in_spreadsheet_searches_cells_case_insensitively(self):
        sheets_service = RecordingSheetsService()
        values_resource = sheets_service.spreadsheets_resource.values_resource
        values_resource.get_results["Sheet1"] = {
            "values": [["Name", "Role"], ["Ada Lovelace", "Engineer"]]
        }
        values_resource.get_results["Data"] = {"values": [["Other"]]}

        result = server.find_in_spreadsheet(
            "spreadsheet-id",
            "ada",
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertEqual(result, [{"sheet": "Sheet1", "cell": "A2", "value": "Ada Lovelace"}])

    def test_add_chart_rejects_invalid_chart_type_before_api_call(self):
        sheets_service = RecordingSheetsService()

        result = server.add_chart(
            "spreadsheet-id",
            "Sheet1",
            "BAD",
            "A1:B2",
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertIn("Invalid chart type", result["error"])
        self.assertEqual(sheets_service.spreadsheets_resource.calls, [])

    def test_add_chart_builds_batch_update_request(self):
        sheets_service = RecordingSheetsService()

        result = server.add_chart(
            "spreadsheet-id",
            "Sheet1",
            "line",
            "A1:B5",
            title="Trend",
            x_axis_label="Month",
            y_axis_label="Value",
            position_x=10,
            position_y=20,
            width=300,
            height=200,
            ctx=fake_ctx(sheets_service=sheets_service),
        )

        self.assertTrue(result["success"])
        _, call = sheets_service.spreadsheets_resource.calls[-1]
        add_chart = call["body"]["requests"][0]["addChart"]["chart"]
        self.assertEqual(call["spreadsheetId"], "spreadsheet-id")
        self.assertEqual(add_chart["spec"]["title"], "Trend")
        self.assertEqual(add_chart["spec"]["basicChart"]["chartType"], "LINE")
        self.assertEqual(
            add_chart["spec"]["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"],
            [
                {
                    "sheetId": 123,
                    "startColumnIndex": 0,
                    "startRowIndex": 0,
                    "endColumnIndex": 1,
                    "endRowIndex": 5,
                }
            ],
        )
        self.assertEqual(
            add_chart["spec"]["basicChart"]["series"][0]["series"]["sourceRange"]["sources"],
            [
                {
                    "sheetId": 123,
                    "startColumnIndex": 1,
                    "startRowIndex": 0,
                    "endColumnIndex": 2,
                    "endRowIndex": 5,
                }
            ],
        )
        self.assertEqual(
            add_chart["position"]["overlayPosition"],
            {
                "anchorCell": {"sheetId": 123, "rowIndex": 0, "columnIndex": 0},
                "offsetXPixels": 10,
                "offsetYPixels": 20,
                "widthPixels": 300,
                "heightPixels": 200,
            },
        )


def fake_dwd_ctx(dwd_credentials, acting_user_email=None):
    """A ctx wired to a real SpreadsheetContext in DWD mode, mirroring what the
    forked spreadsheet_lifespan() yields when DWD_SERVICE_ACCOUNT_CONFIG is set."""
    lifespan_context = server.SpreadsheetContext(dwd_credentials=dwd_credentials)
    meta = SimpleNamespace(actingUserEmail=acting_user_email) if acting_user_email is not None else None
    request_context = SimpleNamespace(lifespan_context=lifespan_context, meta=meta)
    return SimpleNamespace(request_context=request_context)


def fake_build_factory(drive_files_result=None):
    """Stand-in for googleapiclient.discovery.build() that returns MagicMocks
    wired up just enough for list_spreadsheets/list_folders to run for real."""
    drive_files_result = drive_files_result if drive_files_result is not None else {"files": []}

    def fake_build(api, version, credentials, cache_discovery=False):
        service = MagicMock(name=f"{api}-{version}-service")
        service.files.return_value.list.return_value.execute.return_value = drive_files_result
        return service

    return fake_build


class DomainWideDelegationImpersonationTests(unittest.TestCase):
    """Proves the _meta.actingUserEmail -> per-call impersonated credentials
    wiring works end to end, without needing a real DWD-granted service
    account. This is the local validation for the gateway's _meta injection
    contract: the gateway must set _meta.actingUserEmail on every tools/call
    request when DWD is configured, matching what these tests assert on."""

    def test_tool_call_impersonates_the_acting_user_from_meta(self):
        dwd_credentials = MagicMock(name="dwd_base_credentials")
        dwd_credentials.with_subject.return_value = MagicMock(name="impersonated_creds")
        ctx = fake_dwd_ctx(dwd_credentials, acting_user_email="fernanda@privadoadvisors.com")

        with patch.object(server, "build", side_effect=fake_build_factory()):
            result = server.list_spreadsheets(ctx=ctx)

        self.assertEqual(result, [])
        dwd_credentials.with_subject.assert_called_once_with("fernanda@privadoadvisors.com")

    def test_same_acting_user_reuses_cached_services_across_tool_calls(self):
        dwd_credentials = MagicMock(name="dwd_base_credentials")
        dwd_credentials.with_subject.return_value = MagicMock(name="impersonated_creds")
        ctx = fake_dwd_ctx(dwd_credentials, acting_user_email="madison@privadoadvisors.com")

        with patch.object(server, "build", side_effect=fake_build_factory()) as build:
            server.list_spreadsheets(ctx=ctx)
            server.list_folders(ctx=ctx)

        # Two different tools, same acting user, same lifespan_context: one
        # impersonation, one pair of (sheets, drive) service builds — not four.
        dwd_credentials.with_subject.assert_called_once_with("madison@privadoadvisors.com")
        self.assertEqual(build.call_count, 2)  # sheets + drive, built once and cached

    def test_different_acting_users_get_isolated_credentials(self):
        dwd_credentials = MagicMock(name="dwd_base_credentials")
        dwd_credentials.with_subject.side_effect = lambda email: MagicMock(name=f"creds-{email}")
        ctx_a = fake_dwd_ctx(dwd_credentials, acting_user_email="robert@privadoadvisors.com")
        ctx_b = fake_dwd_ctx(dwd_credentials, acting_user_email="fernanda@privadoadvisors.com")
        # Same underlying lifespan_context (as if both requests hit the one shared
        # /mcp bridge session) — only the acting user differs, as it would per-call.
        ctx_b.request_context.lifespan_context = ctx_a.request_context.lifespan_context

        with patch.object(server, "build", side_effect=fake_build_factory()):
            server.list_spreadsheets(ctx=ctx_a)
            server.list_spreadsheets(ctx=ctx_b)

        self.assertEqual(
            dwd_credentials.with_subject.call_args_list,
            [unittest.mock.call("robert@privadoadvisors.com"), unittest.mock.call("fernanda@privadoadvisors.com")],
        )

    def test_missing_acting_user_email_raises_instead_of_guessing(self):
        dwd_credentials = MagicMock(name="dwd_base_credentials")
        ctx = fake_dwd_ctx(dwd_credentials, acting_user_email=None)

        with self.assertRaises(ValueError):
            server.list_spreadsheets(ctx=ctx)

        dwd_credentials.with_subject.assert_not_called()

    def test_contextvar_override_does_not_leak_across_calls(self):
        dwd_credentials = MagicMock(name="dwd_base_credentials")
        dwd_credentials.with_subject.return_value = MagicMock(name="impersonated_creds")
        ctx = fake_dwd_ctx(dwd_credentials, acting_user_email="robert@privadoadvisors.com")

        with patch.object(server, "build", side_effect=fake_build_factory()):
            server.list_spreadsheets(ctx=ctx)

        # After the call returns, the contextvar must be reset — accessing the
        # properties directly (as a bypassing code path, e.g. a resource, would)
        # must hit the "no acting user in scope" guard, not a stale override.
        self.assertIsNone(server._impersonated_services_var.get())
        with self.assertRaises(RuntimeError):
            _ = ctx.request_context.lifespan_context.sheets_service

    def test_non_dwd_mode_is_unaffected(self):
        sheets_service = RecordingSheetsService()
        lifespan_context = server.SpreadsheetContext(_sheets_service=sheets_service, _drive_service=None)
        self.assertIsNone(lifespan_context.dwd_credentials)
        self.assertIs(lifespan_context.sheets_service, sheets_service)


if __name__ == "__main__":
    unittest.main()
