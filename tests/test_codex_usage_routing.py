"""Offline usage routing: inference proxies must not replace saved account hosts."""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quota_providers import codex


class CodexUsageRoutingTests(unittest.TestCase):
    def test_saved_account_usage_ignores_ambient_inference_proxy(self):
        upstream = "https://chatgpt.com/backend-api/codex"
        usage = types.ModuleType("agent.account_usage")
        usage._codex_backend_urls = mock.Mock(return_value=(
            "https://chatgpt.com/backend-api/wham/usage",))
        agent = types.ModuleType("agent")
        agent.account_usage = usage
        auth = types.ModuleType("hermes_cli.auth_codex")
        auth._codex_pool_route_base_url = mock.Mock(
            return_value="http://127.0.0.1:8787/v1")
        auth._codex_base_url = mock.Mock(return_value="http://127.0.0.1:8787/v1")
        modules = {"agent": agent, "agent.account_usage": usage,
                   "hermes_cli.auth_codex": auth}
        with mock.patch.dict(sys.modules, modules):
            result = codex._usage_url(upstream, True)
        self.assertEqual(result, "https://chatgpt.com/backend-api/wham/usage")
        usage._codex_backend_urls.assert_called_once_with(upstream)
        auth._codex_pool_route_base_url.assert_not_called()
        auth._codex_base_url.assert_not_called()

    def test_explicit_gateway_host_is_preserved_for_pool_and_legacy_accounts(self):
        from test_codex_accounts import auth_modules
        gateway = "https://gateway.example.invalid/v1"
        for pooled in (True, False):
            with self.subTest(pooled=pooled):
                modules = auth_modules([])
                usage = modules["agent.account_usage"]
                usage._codex_backend_urls = mock.Mock(return_value=("gateway-usage",))
                auth = modules["hermes_cli.auth_codex"]
                auth._codex_pool_route_base_url = mock.Mock(
                    return_value="http://127.0.0.1:8787/v1")
                with mock.patch.dict(sys.modules, modules):
                    self.assertEqual(codex._usage_url(gateway, pooled), "gateway-usage")
                usage._codex_backend_urls.assert_called_once_with(gateway)
                auth._codex_pool_route_base_url.assert_not_called()

    def test_missing_base_preserves_core_default_resolution(self):
        from test_codex_accounts import auth_modules
        for pooled in (True, False):
            with self.subTest(pooled=pooled):
                modules = auth_modules([])
                auth = modules["hermes_cli.auth_codex"]
                auth._codex_base_url = mock.Mock(
                    return_value="https://gateway.example.invalid/v1")
                auth._codex_pool_route_base_url = mock.Mock(
                    return_value="https://gateway.example.invalid/v1")
                usage = modules["agent.account_usage"]
                usage._codex_backend_urls = mock.Mock(return_value=("default-usage",))
                with mock.patch.dict(sys.modules, modules):
                    self.assertEqual(codex._usage_url(None, pooled), "default-usage")
                if pooled:
                    auth._codex_pool_route_base_url.assert_called_once_with("")
                    auth._codex_base_url.assert_not_called()
                else:
                    auth._codex_base_url.assert_called_once_with()
                    auth._codex_pool_route_base_url.assert_not_called()
                usage._codex_backend_urls.assert_called_once_with(
                    "https://gateway.example.invalid/v1")

    def test_older_core_usage_helper_still_receives_saved_host(self):
        from test_codex_accounts import auth_modules
        modules = auth_modules([])
        usage = modules["agent.account_usage"]
        del usage._codex_backend_urls
        usage._resolve_codex_usage_url = mock.Mock(return_value="legacy-usage")
        upstream = "https://chatgpt.com/backend-api/codex"
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(codex._usage_url(upstream, True), "legacy-usage")
        usage._resolve_codex_usage_url.assert_called_once_with(upstream)


if __name__ == "__main__":
    unittest.main()
