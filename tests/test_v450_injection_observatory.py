from pathlib import Path


class TestStage5ObservatoryFrontend:
    def test_observatory_uses_real_options_url_state_and_shared_pagination(self):
        page = Path("webui/frontend/src/pages/injection/InjectionPage.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/injection.ts").read_text(encoding="utf-8")

        for marker in ("getScopeOptions", "ScopeSelect", "usePaginationSearchParams", "PaginationControls", "QueryState", "config_revision", "session_id"):
            assert marker in page
        assert "channelFilterOptions" not in page
        assert "/api/observatory/traces" in api
        assert "/api/injection/traces" not in api

    def test_trace_detail_uses_complete_payload_viewer_and_opaque_links(self):
        sheet = Path("webui/frontend/src/pages/injection/TraceDetailSheet.tsx").read_text(encoding="utf-8")

        assert "TracePayloadViewer" in sheet
        assert "ObjectDeepLink" in sheet
        assert "rawPayload" in sheet
        assert "50000" not in sheet
        assert "?id=" not in sheet
