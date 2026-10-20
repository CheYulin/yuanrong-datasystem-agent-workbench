"""Materialize a recorded compiler driver for legacy third-party header compatibility."""
import argparse
import json
import os
from pathlib import Path
import shlex

from kvcbench.platform.files import atomic_json, identity, regular


def prepare(root, compiler):
    root = Path(root).resolve(strict=True)
    compiler = Path(compiler).resolve(strict=True)
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError('Executable C++ compiler required')
    directory = root / 'compiler-inputs/datasystem'
    directory.mkdir(parents=True, exist_ok=False)
    wrapper = directory / 'g++'
    options = ['-include', 'cstdint', '-include', 'algorithm']
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([str(compiler), *options]) + ' "$@"\n')
    wrapper.chmod(0o755)
    record = {'schema_version': 1, 'product': 'datasystem',
              'driver': {'path': str(compiler), **identity(compiler)}, 'prefix_arguments': options,
              'wrapper': {'path': str(wrapper.relative_to(root)), **identity(wrapper)},
              'scope': 'C++ header preincludes for GCC 15 compatibility; native feature and optimization options unchanged'}
    atomic_json(directory / 'record.json', record, exclusive=True)
    return record


def verify(root):
    root = Path(root)
    record = json.loads(regular(root, 'compiler-inputs/datasystem/record.json').read_text())
    if (record.get('schema_version') != 1 or record.get('product') != 'datasystem'
            or record.get('prefix_arguments') != ['-include', 'cstdint', '-include', 'algorithm']
            or record.get('wrapper', {}).get('path') != 'compiler-inputs/datasystem/g++'
            or not Path(record['driver']['path']).is_absolute()):
        raise ValueError('Unexpected compiler compatibility contract')
    for key, path in [('driver', Path(record['driver']['path'])),
                      ('wrapper', regular(root, record['wrapper']['path']))]:
        observed = identity(path)
        if any(observed[field] != record[key][field] for field in ('sha256', 'bytes', 'mode')):
            raise ValueError('Compiler input changed: ' + key)
    cache = root / 'datasystem/build/_deps/rocksdb-build/CMakeCache.txt'
    compiler_path = str(root / record['wrapper']['path'])
    if not any(line.startswith('CMAKE_CXX_COMPILER:') and line.split('=', 1)[-1] == compiler_path
               for line in cache.read_text().splitlines()):
        raise ValueError('RocksDB did not use the recorded compatibility compiler driver')
    return {str(path.relative_to(root)): identity(path) for path in
            [root / record['wrapper']['path'], root / 'compiler-inputs/datasystem/record.json']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.compiler)))


if __name__ == '__main__':
    main()
