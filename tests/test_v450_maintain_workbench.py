from pathlib import Path


class TestV450MaintainWorkbench:
    def test_legacy_maintain_workbench_is_not_published(self):
        assert not Path("webui/static/maintain.html").exists()
