"""Compiler compatibility inputs are recorded and checked against CMake's actual driver."""
from pathlib import Path
import tempfile
import unittest

from kvcbench.build.toolchain import prepare, verify


class ToolchainTests(unittest.TestCase):
    def fixture(self, root):
        compiler = root / 'real compiler'
        compiler.write_text('#!/bin/sh\nexit 0\n')
        compiler.chmod(0o755)
        row = prepare(root, compiler)
        cache = root / 'datasystem/build/_deps/rocksdb-build/CMakeCache.txt'
        cache.parent.mkdir(parents=True)
        cache.write_text('CMAKE_CXX_COMPILER:FILEPATH=' + str(root / row['wrapper']['path']) + '\n')
        return row, cache

    def test_recorded_driver_is_exact_and_cmake_must_use_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row, cache = self.fixture(root)
            self.assertEqual(row['prefix_arguments'], ['-include', 'cstdint', '-include', 'algorithm'])
            self.assertEqual(len(verify(root)), 2)
            cache.write_text('CMAKE_CXX_COMPILER:FILEPATH=/unrecorded/g++\n')
            with self.assertRaisesRegex(ValueError, 'did not use'):
                verify(root)

    def test_changed_real_compiler_or_wrapper_is_rejected(self):
        for key in ('driver', 'wrapper'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                row, _ = self.fixture(root)
                path = Path(row[key]['path']) if key == 'driver' else root / row[key]['path']
                path.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    verify(root)


if __name__ == '__main__':
    unittest.main()
