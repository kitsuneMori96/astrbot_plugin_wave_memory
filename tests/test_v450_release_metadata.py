from pathlib import Path


class TestV450ReleaseMetadata:
    def test_metadata_and_register_version_are_v450(self):
        metadata = Path("metadata.yaml").read_text(encoding="utf-8")
        main = Path("main.py").read_text(encoding="utf-8")

        assert "version: v4.5.0" in metadata
        assert '"4.5.0"' in main
        assert '"4.2.1"' not in main

    def test_changelog_has_v450_release_notes(self):
        changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
        assert "## v4.5.0" in changelog
        for marker in (
            "前端优化 + 黑盒管理前端",
            "黑盒管理矩阵",
            "/api/blackbox",
            "BookLore",
            "FewShot",
            "Facts",
            "People",
            "Indexes",
            "注入观测台",
            "维护工作台",
            "全量验证",
        ):
            assert marker in changelog

    def test_readme_badge_and_recent_releases_include_v450(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "version-v4.5.0-blue.svg" in readme
        assert "| **v4.5.0** |" in readme
        assert "前端优化 + 黑盒管理前端" in readme
        assert "黑盒管理矩阵" in readme
