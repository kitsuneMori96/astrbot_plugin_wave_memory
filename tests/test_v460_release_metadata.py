from pathlib import Path


class TestV463ReleaseMetadata:
    def test_metadata_and_register_version_are_v463(self):
        metadata = Path("metadata.yaml").read_text(encoding="utf-8")
        main = Path("main.py").read_text(encoding="utf-8")

        assert "version: v4.6.3" in metadata
        assert '"4.6.3"' in main
        assert '"4.6.0"' not in main

    def test_changelog_has_v463_and_v460_release_notes(self):
        changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
        assert "## v4.6.3" in changelog
        assert "开放 Scope" in changelog or "跨群同文" in changelog
        assert "## v4.6.0" in changelog
        for marker in (
            "Scoped Runtime",
            "Tag 治理",
            "关系与书设兼容",
            "受限 HNSW",
            "Timeline 全历史",
            "767 项",
        ):
            assert marker in changelog

    def test_readme_badge_and_recent_releases_include_v463(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "version-v4.6.3-blue.svg" in readme
        assert "| **v4.6.3** |" in readme
        assert "开放 Scope" in readme or "跨群同文" in readme
