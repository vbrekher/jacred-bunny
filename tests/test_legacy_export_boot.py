"""Execute isolated boot dispatch without root privileges or cloud access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bunny/boot.sh'


class LegacyExportBootTests(unittest.TestCase):
    def test_export_dispatch_precedes_config_changes_and_cron(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'dispatch'
            dispatcher = root / 'su-exec'
            dispatcher.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DISPATCH_MARKER"\n')
            dispatcher.chmod(0o700)
            for command in ('mkdir', 'chown', 'chmod', 'crond', 'cp', 'sed'):
                trap = root / command
                trap.write_text('#!/bin/sh\necho "unexpected mutation" >&2\nexit 97\n')
                trap.chmod(0o700)
            run = subprocess.run(['/bin/sh', str(SCRIPT)], env=dict(os.environ, PATH=str(root),
                JACRED_LEGACY_EXPORT='1', JACRED_BUNNY_CRON='invalid', DISPATCH_MARKER=str(marker)),
                capture_output=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(marker.read_text().splitlines(), ['jacred:jacred', '/app/JacRed', 'legacy-export'])
            self.assertNotIn(b'unexpected mutation', run.stderr)

    def test_invalid_mode_fails_before_dispatch(self):
        run = subprocess.run(['/bin/sh', str(SCRIPT)], env=dict(os.environ,
            JACRED_LEGACY_EXPORT='invalid'), capture_output=True, timeout=5)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(b'JACRED_LEGACY_EXPORT must be 0 or 1', run.stderr)


if __name__ == '__main__':
    unittest.main()
