from pathlib import Path


class TestStage5ImportContract:
    def test_import_page_uses_real_source_preflight_and_durable_job(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/import.ts").read_text(encoding="utf-8")
        for marker in ("getImportSources", "preflightImport", "startImport", "waitForImportJob", "真实预检", "确认创建 Durable Job", "任务状态"):
            assert marker in page + api
        assert "preflight_token" in api
        assert "job_id" in api

    def test_import_page_exposes_real_empty_error_and_partial_states(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/import.ts").read_text(encoding="utf-8")
        for marker in ("QueryState", "empty", "error", "source_status", "result", "error_code", "error_message"):
            assert marker in page + api

    def test_import_execution_does_not_use_legacy_page_or_bare_id_mutation(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")
        assert "/maintain" not in page
        assert "preflight.preflight_token" in page
        assert "job_id" in page
