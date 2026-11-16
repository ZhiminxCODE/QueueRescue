import json
from pathlib import Path
import tempfile
import unittest

from backend.server import static_csp
from backend.service import Problem


class StaticPolicyTests(unittest.TestCase):
    def test_build_policy_allows_only_exact_aws_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertIn("connect-src 'self';", static_csp(base))
            origins = ["https://example123.execute-api.us-east-1.amazonaws.com",
                       "https://example123.auth.us-east-1.amazoncognito.com"]
            (base / 'connect-origins.json').write_text(json.dumps(origins))
            self.assertIn("connect-src 'self' " + ' '.join(origins) + ';', static_csp(base))
            for bad in [["https://evil.example"], [origins[0] + "/path"], ["*"], ["https://x\r\nHeader: injected"], {}, [None]]:
                (base / 'connect-origins.json').write_text(json.dumps(bad))
                with self.assertRaises(Problem):
                    static_csp(base)
