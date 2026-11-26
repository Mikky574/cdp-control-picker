"""Windows sharing conflicts must not corrupt a state file or retry an action."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace import atomic_write


@unittest.skipUnless(os.name == 'nt', 'Windows file sharing behavior')
class AtomicWriteTests(unittest.TestCase):
    def test_brief_real_reader_lock_is_retried_and_original_is_preserved_until_replace(self):
        with tempfile.TemporaryDirectory(prefix='picker-atomic-lock-') as directory:
            path = Path(directory) / 'state.json'
            path.write_text('original', encoding='utf-8')
            blocked = threading.Event()
            replace = os.replace
            def observed(*args):
                try:return replace(*args)
                except PermissionError:
                    blocked.set();raise
            reader = path.open('rb')
            try:
                with patch('workspace.os.replace', side_effect=observed), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(atomic_write, path, 'replacement')
                    try:
                        self.assertTrue(blocked.wait(2), 'Expected a real Windows sharing violation')
                        self.assertEqual(reader.read(), b'original')
                    finally:
                        reader.close()
                    future.result(timeout=3)
            finally:
                reader.close()
            self.assertEqual(path.read_text(encoding='utf-8'), 'replacement')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_permanent_sharing_error_is_bounded_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory(prefix='picker-atomic-denied-') as directory:
            path = Path(directory) / 'state.json'
            path.write_text('original', encoding='utf-8')
            error = PermissionError('fixture sharing conflict')
            error.winerror = 32
            with patch('workspace.os.replace', side_effect=error) as replace, patch('workspace.time.sleep'):
                with self.assertRaises(PermissionError):atomic_write(path, 'replacement')
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(path.read_text(encoding='utf-8'), 'original')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


if __name__ == '__main__':unittest.main()
