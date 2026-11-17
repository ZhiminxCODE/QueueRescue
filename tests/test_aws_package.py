from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.package_aws import build


class AwsPackageTests(unittest.TestCase):
    def test_source_package_is_deterministic_and_excludes_workspace_data(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Path(directory) / 'one.zip', Path(directory) / 'two.zip'
            self.assertEqual(build(one, install=False), build(two, install=False))
            with zipfile.ZipFile(one) as archive:
                names = archive.namelist()
                self.assertIn('cloud/api.py', names)
                self.assertIn('cloud/replay.py', names)
                self.assertIn('cloud/audit_archive.py', names)
                self.assertIn('backend/service.py', names)
                self.assertTrue(all(name.startswith(('cloud/', 'backend/')) and name.endswith('.py') for name in names))
                self.assertFalse(any('.data' in name or '.aws' in name or 'tests/' in name for name in names))
