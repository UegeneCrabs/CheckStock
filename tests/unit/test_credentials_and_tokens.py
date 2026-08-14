import base64
import json
import logging
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from app.ff_import import google_service_account
from app.ozon import tokens as ozon_tokens
from app.wb import token_watch
from app.wb import tokens as wb_tokens
from app.yandex import tokens as yandex_tokens


class MarketplaceTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        wb_tokens.reload_tokens()
        ozon_tokens.reload_tokens()
        logging.disable(logging.NOTSET)
        self.temp.cleanup()

    def test_wb_tokens_valid_missing_and_invalid(self) -> None:
        path = self.root / "wb.json"
        path.write_text(json.dumps({"good": "token", "empty": ""}), encoding="utf-8")
        with mock.patch.object(wb_tokens, "TOKENS_PATH", path):
            wb_tokens.reload_tokens()
            self.assertEqual(wb_tokens.get_token("good"), "token")
            self.assertTrue(wb_tokens.is_listed("empty"))
            self.assertFalse(wb_tokens.has_token("empty"))
            with self.assertRaises(wb_tokens.TokenNotFoundError):
                wb_tokens.get_token("missing")
            self.assertIsNotNone(wb_tokens._tokens_signature())
            wb_tokens.reload_tokens()

        missing = self.root / "missing.json"
        with mock.patch.object(wb_tokens, "TOKENS_PATH", missing):
            wb_tokens.reload_tokens()
            self.assertEqual(wb_tokens._load_tokens(), {})

        for index, value in enumerate(("", "{broken", "[]")):
            invalid = self.root / f"invalid-{index}.json"
            invalid.write_text(value, encoding="utf-8")
            with mock.patch.object(wb_tokens, "TOKENS_PATH", invalid):
                wb_tokens.reload_tokens()
                self.assertEqual(wb_tokens._load_tokens(), {})

    def test_wb_jwt_helpers(self) -> None:
        expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
        encoded = base64.urlsafe_b64encode(json.dumps({"exp": expires, "x": 1}).encode()).decode().rstrip("=")
        token = f"header.{encoded}.signature"
        self.assertEqual(wb_tokens.decode_token_claims(token)["x"], 1)
        self.assertEqual(int(wb_tokens.get_token_expiry(token).timestamp()), expires)
        self.assertEqual(wb_tokens.decode_token_claims("bad"), {})
        self.assertIsNone(wb_tokens.get_token_expiry("bad"))
        wrong = base64.urlsafe_b64encode(json.dumps({"exp": "bad"}).encode()).decode().rstrip("=")
        self.assertIsNone(wb_tokens.get_token_expiry(f"a.{wrong}.b"))

    def test_ozon_tokens_valid_missing_and_invalid(self) -> None:
        path = self.root / "ozon.json"
        path.write_text(
            json.dumps({"good": {"client_id": " 1 ", "api_key": " key "}, "empty": {}}),
            encoding="utf-8",
        )
        with mock.patch.object(ozon_tokens, "TOKENS_PATH", path):
            ozon_tokens.reload_tokens()
            self.assertEqual(ozon_tokens.get_credentials("good"), ("1", "key"))
            self.assertTrue(ozon_tokens.is_listed("empty"))
            self.assertTrue(ozon_tokens.has_credentials("good"))
            self.assertFalse(ozon_tokens.has_credentials("empty"))
            with self.assertRaises(ozon_tokens.OzonCredentialsNotFoundError):
                ozon_tokens.get_credentials("empty")

        for index, value in enumerate(("", "{broken", "[]")):
            invalid = self.root / f"ozon-invalid-{index}.json"
            invalid.write_text(value, encoding="utf-8")
            with mock.patch.object(ozon_tokens, "TOKENS_PATH", invalid):
                ozon_tokens.reload_tokens()
                self.assertEqual(ozon_tokens._load_tokens(), {})
        with mock.patch.object(ozon_tokens, "TOKENS_PATH", self.root / "missing"):
            ozon_tokens.reload_tokens()
            self.assertIsNone(ozon_tokens._tokens_signature())

    def test_yandex_tokens_all_shapes(self) -> None:
        path = self.root / "yandex.json"
        path.write_text(
            json.dumps(
                {
                    "good": {
                        "api_key": " key ",
                        "business_id": "7",
                        "campaigns": [
                            {"id": "1", "scheme": "FBY", "name": ""},
                            {"id": 2, "scheme": "fbs", "name": "Two"},
                            {"id": "bad"},
                            "wrong",
                        ],
                    },
                    "bad": {"business_id": "x", "campaigns": {}},
                    "wrong": "value",
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(yandex_tokens, "SECRETS_PATH", path):
            self.assertTrue(yandex_tokens.is_listed("good"))
            self.assertTrue(yandex_tokens.has_credentials("good"))
            self.assertFalse(yandex_tokens.has_credentials("bad"))
            self.assertEqual(yandex_tokens.get_api_key("good"), "key")
            with self.assertRaises(KeyError):
                yandex_tokens.get_api_key("bad")
            self.assertEqual(yandex_tokens.get_business_id("good"), 7)
            self.assertIsNone(yandex_tokens.get_business_id("bad"))
            campaigns = yandex_tokens.get_campaigns("good")
            self.assertEqual(len(campaigns), 2)
            self.assertEqual(campaigns[0]["scheme_key"], "fbo")
            self.assertEqual(campaigns[1]["scheme_key"], "fbs")
            self.assertEqual(yandex_tokens.scheme_label(campaigns[1]), "FBS — склады продавца")
            self.assertEqual(yandex_tokens.get_campaigns("bad"), [])
            self.assertEqual(yandex_tokens.stores_with_credentials(), ["good"])
            self.assertEqual(yandex_tokens.scheme_key("", 9), "fbs")

        for index, value in enumerate(("{bad", "[]")):
            invalid = self.root / f"ya-invalid-{index}.json"
            invalid.write_text(value, encoding="utf-8")
            with mock.patch.object(yandex_tokens, "SECRETS_PATH", invalid):
                self.assertEqual(yandex_tokens._load(), {})
        with mock.patch.object(yandex_tokens, "SECRETS_PATH", self.root / "missing"):
            self.assertEqual(yandex_tokens._load(), {})


class TokenWatchTests(unittest.TestCase):
    def test_refresh_schedule_and_warnings(self) -> None:
        now = datetime(2026, 8, 9, 12, tzinfo=UTC)
        with (
            mock.patch.object(token_watch, "STORES", {"good": {}, "none": {}}),
            mock.patch.object(token_watch, "_now", return_value=now),
            mock.patch.object(token_watch.wb_tokens, "has_token", side_effect=lambda slug: slug == "good"),
            mock.patch.object(token_watch.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                token_watch.wb_tokens, "get_token_expiry", return_value=now + timedelta(days=3)
            ),
            mock.patch.object(token_watch.db, "upsert_wb_token_info") as upsert,
        ):
            self.assertEqual(token_watch.refresh_token_info(), 1)
        upsert.assert_called_once()

        with mock.patch.object(token_watch, "_now", return_value=now):
            self.assertTrue(token_watch.should_refresh(None))
            self.assertTrue(token_watch.should_refresh("broken"))
            self.assertTrue(token_watch.should_refresh((now - timedelta(days=8)).isoformat()))
            self.assertTrue(token_watch.should_refresh((now - timedelta(days=1)).isoformat()))
            self.assertFalse(token_watch.should_refresh(now.isoformat()))

        infos = [
            {"store_slug": "good", "expires_at": (now + timedelta(days=2)).isoformat()},
            {"store_slug": "expired", "expires_at": (now - timedelta(days=1)).isoformat()},
            {"store_slug": "far", "expires_at": (now + timedelta(days=30)).isoformat()},
            {"store_slug": "bad", "expires_at": "bad"},
            {"store_slug": "none", "expires_at": None},
        ]
        with (
            mock.patch.object(token_watch, "_now", return_value=now),
            mock.patch.object(token_watch, "STORES", {"good": {"name": "Good"}}),
            mock.patch.object(token_watch.db, "get_wb_token_infos", return_value=infos),
        ):
            warnings = token_watch.get_warnings()
        self.assertEqual([item["store"] for item in warnings], ["expired", "Good"])
        self.assertTrue(warnings[0]["expired"])


class GoogleCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "credentials.json"
        google_service_account.reload_credentials()

    def tearDown(self) -> None:
        google_service_account.reload_credentials()
        self.temp.cleanup()

    def test_key_data_email_and_credentials(self) -> None:
        self.path.write_text(json.dumps({"client_email": "service@test"}), encoding="utf-8")
        credentials = mock.Mock()
        service_account = types.SimpleNamespace(
            Credentials=types.SimpleNamespace(from_service_account_file=mock.Mock(return_value=credentials))
        )
        google = types.ModuleType("google")
        oauth2 = types.ModuleType("google.oauth2")
        oauth2.service_account = service_account
        google.oauth2 = oauth2
        with (
            mock.patch.object(google_service_account, "CREDENTIALS_PATH", self.path),
            mock.patch.dict(sys.modules, {"google": google, "google.oauth2": oauth2}),
        ):
            self.assertTrue(google_service_account.has_credentials())
            self.assertEqual(google_service_account.get_service_account_email(), "service@test")
            self.assertIs(google_service_account.get_credentials(), credentials)

    def test_missing_dependency_and_invalid_key(self) -> None:
        original_import = __import__

        def missing(name, *args, **kwargs):
            if name == "google.oauth2":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with (
            mock.patch.object(google_service_account, "CREDENTIALS_PATH", self.path),
            mock.patch("builtins.__import__", side_effect=missing),
        ):
            with self.assertRaises(google_service_account.CredentialsUnavailableError):
                google_service_account.get_credentials()

        google_service_account.reload_credentials()
        self.path.write_text("{bad", encoding="utf-8")
        with mock.patch.object(google_service_account, "CREDENTIALS_PATH", self.path):
            self.assertEqual(google_service_account.get_service_account_email(), "?")


if __name__ == "__main__":
    unittest.main()
