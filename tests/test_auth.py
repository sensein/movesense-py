"""Tests for token authentication."""

from pathlib import Path
from unittest.mock import patch

from movesense.server.auth import get_or_create_token, TOKEN_FILE


class TestTokenAuth:
    def test_generates_32_char_hex_token(self, tmp_path):
        token_file = tmp_path / "token"
        with patch("movesense.server.auth.TOKEN_FILE", token_file):
            with patch("movesense.server.auth.CONFIG_DIR", tmp_path):
                token = get_or_create_token()
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_persists_token_to_file(self, tmp_path):
        token_file = tmp_path / "token"
        with patch("movesense.server.auth.TOKEN_FILE", token_file):
            with patch("movesense.server.auth.CONFIG_DIR", tmp_path):
                token = get_or_create_token()
        assert token_file.read_text().strip() == token

    def test_reuses_existing_token(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("existingtoken1234567890abcdef12")
        with patch("movesense.server.auth.TOKEN_FILE", token_file):
            with patch("movesense.server.auth.CONFIG_DIR", tmp_path):
                token = get_or_create_token()
        assert token == "existingtoken1234567890abcdef12"

    def test_regenerates_if_file_empty(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("")
        with patch("movesense.server.auth.TOKEN_FILE", token_file):
            with patch("movesense.server.auth.CONFIG_DIR", tmp_path):
                token = get_or_create_token()
        assert len(token) == 32
