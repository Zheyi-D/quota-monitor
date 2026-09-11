"""Source gate changes only quota WeCom sending, never the poll schedule."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

class SourceSwitchTest(unittest.TestCase):
    def test_gate(self):
        # Execute the actual small URL selector without importing the CI entry side effects.
        import ast
        source = Path("ci_run.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_wecom_webhook_urls")
        namespace = {"os": os}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "ci_run.py", "exec"), namespace)
        with patch.dict(os.environ, {"QUOTA_WECOM_SOURCE": "observer", "WECOM_WEBHOOK_URL": "mock"}):
            self.assertEqual(namespace["_wecom_webhook_urls"](), [])
        with patch.dict(os.environ, {"QUOTA_WECOM_SOURCE": "legacy", "WECOM_WEBHOOK_URL": "mock,mock"}):
            self.assertEqual(namespace["_wecom_webhook_urls"](), ["mock"])
        self.assertIn("legacy_wecom else []", source)
        workflow = Path(".github/workflows/fetch.yml").read_text(encoding="utf-8")
        self.assertIn("vars.QUOTA_WECOM_SOURCE", workflow)
        self.assertIn('POLL_INTERVAL_SECONDS: "30"', workflow)
