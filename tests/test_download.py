# -*- coding: utf-8 -*-
"""Tests for XHS note download engine."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest
import requests
import requests.exceptions

from agent_reach.download import (
    _download_file,
    _ensure_dict,
    _extract_video_urls,
    _parse_mcporter_output,
    _parse_xhs_url,
    _resolve_short_url,
    download_xhs_note,
)


# ---------------------------------------------------------------------------
# URL parsing tests
# ---------------------------------------------------------------------------


class TestParseXhsUrl:
    def test_explore_with_xsec(self):
        url = "https://www.xiaohongshu.com/explore/64b95d01000000000c034587?xsec_token=abc123&xsec_source=pc_feed"
        result = _parse_xhs_url(url)
        assert result["note_id"] == "64b95d01000000000c034587"
        assert result["xsec_token"] == "abc123"

    def test_explore_no_xsec(self):
        url = "https://www.xiaohongshu.com/explore/64b95d01000000000c034587"
        result = _parse_xhs_url(url)
        assert result["note_id"] == "64b95d01000000000c034587"
        assert result["xsec_token"] == ""

    def test_discovery_item(self):
        url = "https://www.xiaohongshu.com/discovery/item/abc123def?xsec_token=xyz"
        result = _parse_xhs_url(url)
        assert result["note_id"] == "abc123def"
        assert result["xsec_token"] == "xyz"

    def test_rednote_com(self):
        """International XHS domain."""
        url = "https://www.rednote.com/explore/64b95d01000000000c034587?xsec_token=tok"
        result = _parse_xhs_url(url)
        assert result["note_id"] == "64b95d01000000000c034587"
        assert result["xsec_token"] == "tok"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="无法从 URL 提取 note_id"):
            _parse_xhs_url("https://www.example.com/not/a/valid/path")

    def test_trailing_slash(self):
        url = "https://www.xiaohongshu.com/explore/64b95d01000000000c034587/"
        result = _parse_xhs_url(url)
        assert result["note_id"] == "64b95d01000000000c034587"


class TestResolveShortUrl:
    def test_resolves_redirect(self):
        with patch("requests.head") as mock_head:
            mock_resp = MagicMock()
            mock_resp.url = "https://www.xiaohongshu.com/explore/abc123?xsec_token=tok"
            mock_head.return_value = mock_resp
            result = _resolve_short_url("https://xhslink.com/abc")
            assert "xiaohongshu.com/explore/abc123" in result

    def test_returns_original_on_error(self):
        with patch("requests.head", side_effect=requests.RequestException("timeout")):
            result = _resolve_short_url("https://xhslink.com/abc")
            assert result == "https://xhslink.com/abc"

    def test_skips_non_short_urls(self):
        url = "https://www.xiaohongshu.com/explore/abc123"
        result = _resolve_short_url(url)
        assert result == url


# ---------------------------------------------------------------------------
# Mcporter output parser tests
# ---------------------------------------------------------------------------


class TestParseMcporterOutput:
    def test_bare_json(self):
        raw = '{"title": "hello", "desc": "world"}'
        result = _parse_mcporter_output(raw)
        assert result == {"title": "hello", "desc": "world"}

    def test_result_wrapped(self):
        raw = 'Result: {"note_id": "abc", "title": "test"}'
        result = _parse_mcporter_output(raw)
        assert result == {"note_id": "abc", "title": "test"}

    def test_text_prefix_and_suffix(self):
        raw = 'Some text prefix\n{"title": "hello"}\n more text'
        result = _parse_mcporter_output(raw)
        assert result == {"title": "hello"}

    def test_nested_json(self):
        raw = '{"note": {"title": "nested", "tags": ["a", "b"]}}'
        result = _parse_mcporter_output(raw)
        assert result == {"note": {"title": "nested", "tags": ["a", "b"]}}

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="无法从 mcporter 输出中解析 JSON"):
            _parse_mcporter_output("no json here at all")


# ---------------------------------------------------------------------------
# _ensure_dict tests
# ---------------------------------------------------------------------------


class TestEnsureDict:
    def test_dict_passthrough(self):
        data = {"note_id": "123"}
        assert _ensure_dict(data, "test") == data

    def test_list_takes_first(self):
        data = [{"note_id": "123"}, {"note_id": "456"}]
        result = _ensure_dict(data, "test")
        assert result == {"note_id": "123"}

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="返回了空列表"):
            _ensure_dict([], "test-backend")

    def test_non_dict_scalar(self):
        result = _ensure_dict("just a string", "test")
        assert result == {"note": "just a string"}


# ---------------------------------------------------------------------------
# File download tests
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_success(self, tmp_path):
        dest = tmp_path / "test.jpg"
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.iter_content = MagicMock(return_value=[b"data"])
            mock_get.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_get.return_value.__exit__ = MagicMock(return_value=False)
            # requests.get returns a Response directly, not a context manager
            mock_get.return_value = mock_resp

            ok = _download_file("https://example.com/img.jpg", dest)
            assert ok
            assert dest.exists()
            assert dest.read_bytes() == b"data"

    def test_404_no_retry(self, tmp_path):
        dest = tmp_path / "test.jpg"
        with patch("requests.get") as mock_get:
            resp_404 = MagicMock()
            resp_404.status_code = 404
            mock_get.side_effect = requests.HTTPError(response=resp_404)

            ok = _download_file("https://example.com/img.jpg", dest)
            assert not ok
            assert mock_get.call_count == 1

    def test_403_no_retry(self, tmp_path):
        dest = tmp_path / "test.jpg"
        with patch("requests.get") as mock_get:
            resp_403 = MagicMock()
            resp_403.status_code = 403
            mock_get.side_effect = requests.HTTPError(response=resp_403)

            ok = _download_file("https://example.com/img.jpg", dest)
            assert not ok
            assert mock_get.call_count == 1

    def test_retry_on_connection_error(self, tmp_path):
        dest = tmp_path / "test.jpg"

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise requests.ConnectionError("connection refused")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.iter_content = MagicMock(return_value=[b"ok"])
            return resp

        with patch("requests.get", side_effect=side_effect):
            ok = _download_file("https://example.com/img.jpg", dest)
            assert ok
            assert call_count[0] == 3


# ---------------------------------------------------------------------------
# Video URL extraction tests
# ---------------------------------------------------------------------------


class TestExtractVideoUrls:
    def test_video_url_field(self):
        data = {"video_url": "https://example.com/video.mp4"}
        assert _extract_video_urls(data) == ["https://example.com/video.mp4"]

    def test_video_field(self):
        data = {"video": "https://example.com/v.mp4"}
        assert _extract_video_urls(data) == ["https://example.com/v.mp4"]

    def test_video_urls_list(self):
        data = {"video_urls": [
            "https://example.com/v1.mp4",
            "https://example.com/v2.mp4",
        ]}
        assert _extract_video_urls(data) == [
            "https://example.com/v1.mp4",
            "https://example.com/v2.mp4",
        ]

    def test_no_video(self):
        data = {"title": "just text"}
        assert _extract_video_urls(data) == []

    def test_video_dict_with_url(self):
        data = {"video_urls": [
            {"url": "https://example.com/v.mp4", "quality": "1080p"},
        ]}
        assert _extract_video_urls(data) == ["https://example.com/v.mp4"]

    def test_non_http_skipped(self):
        data = {"video_url": "not-a-url"}
        assert _extract_video_urls(data) == []


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OpenCLI field-list parsing tests
# ---------------------------------------------------------------------------


class TestOpencliFieldlist:
    """OpenCLI's real `note -f yaml` returns a [{field, value}, ...] list."""

    FIELDLIST_YAML = (
        "- field: title\n  value: 支点越多越稳。\n"
        "- field: author\n  value: 夏知非fifi\n"
        "- field: content\n  value: 一些正文内容\n"
        "- field: likes\n  value: '1166'\n"
        "- field: collects\n  value: '200'\n"
        "- field: comments\n  value: '10'\n"
        "- field: tags\n  value: '#自我成长, #生活碎片'\n"
    )

    def test_fieldlist_detected(self):
        import yaml as _yaml
        from agent_reach.download import _is_opencli_fieldlist
        rows = _yaml.safe_load(self.FIELDLIST_YAML)
        assert _is_opencli_fieldlist(rows) is True

    def test_dict_and_empty_not_fieldlist(self):
        from agent_reach.download import _is_opencli_fieldlist
        assert _is_opencli_fieldlist({"title": "x"}) is False
        assert _is_opencli_fieldlist([]) is False

    def test_fieldlist_parsed_to_schema(self):
        import yaml as _yaml
        from agent_reach.download import _opencli_fieldlist_to_note
        rows = _yaml.safe_load(self.FIELDLIST_YAML)
        note = _opencli_fieldlist_to_note(
            rows, "https://www.xiaohongshu.com/explore/abc123?xsec_token=t"
        )
        assert note["note_id"] == "abc123"
        assert note["title"] == "支点越多越稳。"
        assert note["user"]["nickname"] == "夏知非fifi"
        assert note["desc"] == "一些正文内容"
        assert note["liked_count"] == 1166
        assert note["collected_count"] == 200
        assert note["comment_count"] == 10
        assert note["tags"] == ["#自我成长", "#生活碎片"]
        assert note["images"] == []

    def test_wan_count_coerced(self):
        from agent_reach.download import _coerce_count
        assert _coerce_count("2.3万") == 23000
        assert _coerce_count("3730") == 3730
        assert _coerce_count(5) == 5


class TestParseXhsUrlExtra:
    def test_search_result_url(self):
        from agent_reach.download import _parse_xhs_url
        url = "https://www.xiaohongshu.com/search_result/abc123?xsec_token=tok"
        assert _parse_xhs_url(url)["note_id"] == "abc123"

    def test_user_profile_url(self):
        from agent_reach.download import _parse_xhs_url
        url = (
            "https://www.xiaohongshu.com/user/profile/57ea22315e87e707c0bc01f1/"
            "693c2742000000001f0063fb?xsec_token=tok"
        )
        result = _parse_xhs_url(url)
        assert result["note_id"] == "693c2742000000001f0063fb"
        assert result["xsec_token"] == "tok"


class TestDownloadXhsNote:
    """End-to-end tests with mocked upstream tools."""

    SAMPLE_OPENCLI_YAML = """
title: "测试笔记标题"
desc: "这是一篇测试笔记"
type: normal
note_id: "abc123"
user:
    nickname: "测试用户"
    user_id: "user_001"
image_list:
    - url: "https://example.com/img/0.jpg"
    - url: "https://example.com/img/1.jpg"
tag_list:
    - name: "测试"
liked_count: "100"
collected_count: "50"
comment_count: "20"
share_count: "5"
"""

    def test_no_backend_raises(self, monkeypatch):
        """Raises RuntimeError when no XHS backend is active."""
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.check",
            lambda self, config: None,
        )
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.active_backend",
            None,
        )
        with pytest.raises(RuntimeError, match="没有可用的小红书后端"):
            download_xhs_note("https://www.xiaohongshu.com/explore/abc123")

    def test_opencli_backend_success(self, tmp_path, monkeypatch):
        """Full flow with OpenCLI backend and image downloads."""
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.check",
            lambda self, config: None,
        )
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.active_backend",
            "OpenCLI",
        )

        # Mock subprocess.run for opencli
        def fake_run(cmd, **kwargs):
            if "note" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=self.SAMPLE_OPENCLI_YAML,
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)

        # Mock image downloads
        def fake_download_file(url, dest, **kwargs):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-image-data")
            return True

        monkeypatch.setattr(
            "agent_reach.download._download_file", fake_download_file
        )

        result = download_xhs_note(
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
            save_path=tmp_path,
            download_comments=False,
        )

        # Check directory structure
        note_dir = tmp_path / "abc123"
        assert result == note_dir
        assert note_dir.exists()

        # Check metadata.json
        metadata_path = note_dir / "metadata.json"
        assert metadata_path.exists()
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert meta["note_id"] == "abc123"
        assert meta["title"] == "测试笔记标题"
        assert len(meta["images"]) == 2

        # Check images
        assert (note_dir / "images" / "0.jpg").exists()
        assert (note_dir / "images" / "1.jpg").exists()

    def test_opencli_fieldlist_uses_native_download(self, tmp_path, monkeypatch):
        """Real OpenCLI note (field-list, no media URLs) → native media fetch."""
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.check",
            lambda self, config: None,
        )
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.active_backend",
            "OpenCLI",
        )

        fieldlist = (
            "- field: title\n  value: 支点越多越稳。\n"
            "- field: author\n  value: 夏知非fifi\n"
            "- field: content\n  value: 正文\n"
            "- field: likes\n  value: '1166'\n"
            "- field: tags\n  value: '#a, #b'\n"
        )

        def fake_run(cmd, **kwargs):
            if "note" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=fieldlist, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)

        def fake_media(url, note_dir, **kwargs):
            d = note_dir / "images"
            d.mkdir(parents=True, exist_ok=True)
            (d / "0.jpg").write_bytes(b"x")
            (d / "1.jpg").write_bytes(b"y")
            return ["images/0.jpg", "images/1.jpg"], []

        monkeypatch.setattr(
            "agent_reach.download._download_media_opencli", fake_media
        )

        result = download_xhs_note(
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
            save_path=tmp_path,
            download_comments=False,
        )

        note_dir = tmp_path / "abc123"
        assert result == note_dir
        meta = json.loads((note_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["title"] == "支点越多越稳。"
        assert meta["images"] == ["images/0.jpg", "images/1.jpg"]
        assert meta["type"] == "normal"
        assert (note_dir / "images" / "0.jpg").exists()
        assert (note_dir / "images" / "1.jpg").exists()

    def test_invalid_url_raises_value_error(self, monkeypatch):
        """Invalid URL raises ValueError."""
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.check",
            lambda self, config: None,
        )
        monkeypatch.setattr(
            "agent_reach.channels.xiaohongshu.XiaoHongShuChannel.active_backend",
            "OpenCLI",
        )
        with pytest.raises(ValueError, match="无法从 URL 提取 note_id"):
            download_xhs_note("https://www.notxhs.com/stuff")


class TestCliDownload:
    """CLI integration tests."""

    def test_download_subcommand_exists(self, capsys):
        """CLI shows download help."""
        from agent_reach.cli import main
        with patch("sys.argv", ["agent-reach", "download"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # Should exit 0 and print usage
            captured = capsys.readouterr()
            assert exc_info.value.code == 0

    def test_download_xhs_no_args(self, capsys):
        """download xhs with no URL shows argparse error."""
        from agent_reach.cli import main
        with patch("sys.argv", ["agent-reach", "download", "xhs"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0

    def test_download_xhs_success(self, capsys, tmp_path, monkeypatch):
        """CLI download xhs with mocked download_xhs_note."""
        from agent_reach.cli import main

        # Create a mock note directory with metadata.json
        note_dir = tmp_path / "abc123"
        note_dir.mkdir()
        meta = {
            "note_id": "abc123",
            "title": "CLI Test Note",
            "type": "normal",
            "images": ["https://example.com/0.jpg"],
        }
        (note_dir / "metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        def fake_download(*args, **kwargs):
            return note_dir

        monkeypatch.setattr(
            "agent_reach.download.download_xhs_note", fake_download
        )

        with patch("sys.argv", [
            "agent-reach", "download", "xhs",
            "https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
            "--no-comments",
        ]):
            main()
        captured = capsys.readouterr()
        assert "笔记已下载到" in captured.out
        assert "CLI Test Note" in captured.out
