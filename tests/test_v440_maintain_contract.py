from pathlib import Path


class TestV440MaintainContract:
    def test_legacy_maintain_html_is_retired(self):
        assert not Path("webui/static/maintain.html").exists()
