"""Verify reproduction source bytes against the actual executing postprocessing package."""
from pathlib import Path
import zipfile

from kvcbench.platform.files import regular
from kvcbench.platform.package import verify_zip


def verify_bundle(archive, tool_root):
    manifest = verify_zip(archive)
    with zipfile.ZipFile(archive) as source:
        files = manifest['files']
        selected = set()
        for row in files:
            path = Path(row['path'])
            if path.parts[0] == 'kvcbench':
                selected.add(row['path'])
                if regular(tool_root, row['path']).read_bytes() != source.read('kvc-fair/' + row['path']):
                    raise ValueError('Executing postprocessing source differs from the supplied reproduction package: ' + row['path'])
        actual = {path.relative_to(tool_root).as_posix() for path in (Path(tool_root) / 'kvcbench').rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts}
        if actual != selected:
            raise ValueError('Reproduction archive omits or adds executing package source files')
    return {'verified': True, 'files': len(files), 'package_files': len(selected),
            'scope': 'Exact executing postprocessing package bytes and complete source ZIP manifest; measured runtime sources remain separately frozen.'}
