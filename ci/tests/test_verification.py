import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from ci.verification import current_source, verify_baseline, verify_packages, frontend_assets


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        (self.root / 'app').mkdir()
        self.mail = self.root / 'app/mail.py'
        self.mail.write_text('original notification')
        self.commit()
        self.baseline = {'files': {'app/mail.py': hashlib.sha256(self.mail.read_bytes()).hexdigest()}}

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'test inputs')

    def test_committed_mail_edit_is_a_valid_new_build(self):
        before = current_source(self.root)
        self.mail.write_text('updated notification')
        self.commit()
        after = current_source(self.root)
        self.assertNotEqual(before[0], after[0])
        self.assertNotEqual(before[2], after[2])
        self.assertEqual(after[1]['app/mail.py'], hashlib.sha256(self.mail.read_bytes()).hexdigest())

    def test_new_tracked_application_file_is_included(self):
        (self.root / 'app/new.py').write_text('new application file')
        self.commit()
        self.assertIn('app/new.py', current_source(self.root)[1])

    def test_dirty_inputs_are_rejected(self):
        self.mail.write_text('not committed')
        with self.assertRaisesRegex(ValueError, 'uncommitted'):
            current_source(self.root)

    def test_incorrect_commit_provenance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Declared source commit'):
            current_source(self.root, '0' * 40)

    def test_historical_check_is_explicit_and_still_detects_changes(self):
        verify_baseline(self.root, self.baseline)
        self.mail.write_text('new text')
        self.commit()
        current_source(self.root)
        with self.assertRaisesRegex(ValueError, 'historical release baseline'):
            verify_baseline(self.root, self.baseline)


class ArtifactTests(unittest.TestCase):
    def test_packages_follow_the_current_lock_file(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'requirements.lock.txt'
            lock.write_text('package==2.0\n')
            verify_packages(['package==2.0'], lock)
            with self.assertRaisesRegex(ValueError, 'lock file'):
                verify_packages(['package==1.0'], lock)

    def test_new_frontend_assets_are_allowed_and_recorded(self):
        hashes = 'abc /srv/index.html\ndef /srv/assets/new.js\n'
        self.assertEqual(frontend_assets(hashes)['assets/new.js'], 'def')
        with self.assertRaisesRegex(ValueError, 'historical release baseline'):
            frontend_assets(hashes, {'index.html': 'old'})

    def test_missing_frontend_bundle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'JavaScript bundle'):
            frontend_assets('abc /srv/index.html\n')


if __name__ == '__main__':
    unittest.main()
