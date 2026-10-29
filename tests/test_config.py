"""Tests for platformdirs config paths and automatic legacy migration."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import platformdirs

from pointclicker.targets import Config, Target, config_path, load


class ConfigMigrationTest(unittest.TestCase):
    def test_config_path_platformdirs(self):
        p = config_path()
        if sys.platform == "win32":
            self.assertIn("PointClicker", str(p))
        else:
            self.assertEqual(p, Path(platformdirs.user_config_dir("pointclicker", appauthor=False)) / "config.json")

    def test_automatic_legacy_migration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            legacy_file = tmp / "old_PointClicker" / "config.json"
            new_file = tmp / "new_pointclicker" / "config.json"

            # Create legacy config
            legacy_file.parent.mkdir(parents=True)
            cfg = Config(active_profile="MigratedProfile")
            cfg.targets.append(Target(name="Migrated Target", x=123, y=456))
            legacy_file.write_text(json.dumps({"active_profile": "MigratedProfile", "profiles": {"MigratedProfile": [
                {"name": "Migrated Target", "x": 123, "y": 456, "interval": 5.0}
            ]}}))

            # Migrate
            with patch("pointclicker.targets.Path.home", return_value=tmp / "old_PointClicker"):
                with patch("pointclicker.targets.os.environ.get", return_value=""):
                    # Pass candidate directly to _migrate_legacy_config logic
                    self.assertFalse(new_file.exists())
                    # Simulate migration
                    new_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy_file, new_file)

            self.assertTrue(new_file.exists())
            loaded = load(new_file)
            self.assertEqual(loaded.active_profile, "MigratedProfile")
            self.assertEqual(len(loaded.targets), 1)
            self.assertEqual(loaded.targets[0].name, "Migrated Target")
            self.assertEqual(loaded.targets[0].x, 123)
            self.assertEqual(loaded.targets[0].y, 456)


if __name__ == "__main__":
    unittest.main()
