"""An existing Coordinator can only be adopted from the original hashed build tar."""
from copy import deepcopy
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from kvcbench.build.adoption import record, verify
from kvcbench.core.artifact_extension import COORDINATOR
from kvcbench.platform.files import identity, atomic_json


class CoordinatorAdoptionTests(unittest.TestCase):
    def fixture(self, root):
        coordinator = root / COORDINATOR
        coordinator.parent.mkdir(parents=True)
        coordinator.write_bytes(b'synthetic coordinator native output, never executed')
        coordinator.chmod(0o755)
        archive = root / 'datasystem/output/original-build.tar.gz'
        with tarfile.open(archive, 'w:gz') as target:
            member = tarfile.TarInfo('datasystem/service/datasystem_coordinator')
            member.size = coordinator.stat().st_size
            member.mode = 0o755
            target.addfile(member, io.BytesIO(coordinator.read_bytes()))
        relative = archive.relative_to(root).as_posix()
        original = {'product': 'datasystem', 'tag': 'synthetic', 'sha': '0' * 40, 'build_exit': 0,
                    'artifact_hashes': {relative: identity(archive)['sha256']}, 'artifact_sizes': {relative: archive.stat().st_size}}
        atomic_json(root / 'build-metadata/datasystem.json', original)
        current = deepcopy(original)
        current['artifact_hashes'][COORDINATOR] = identity(coordinator)['sha256']
        current['artifact_sizes'][COORDINATOR] = coordinator.stat().st_size
        return original, current

    def test_adoption_is_exact_original_archive_member_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, current = self.fixture(root)
            before = (root / 'build-metadata/datasystem.json').read_bytes()
            adopted = record(root, original, current)
            self.assertEqual(verify(root, adopted)['artifact'], COORDINATOR)
            self.assertEqual((root / 'build-metadata/datasystem.json').read_bytes(), before)

    def test_changed_coordinator_bytes_cannot_be_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, current = self.fixture(root)
            (root / COORDINATOR).write_bytes(b'different unbound binary')
            current['artifact_hashes'][COORDINATOR] = identity(root / COORDINATOR)['sha256']
            current['artifact_sizes'][COORDINATOR] = (root / COORDINATOR).stat().st_size
            with self.assertRaises(ValueError):
                record(root, original, current)

    def test_another_output_or_modified_original_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, current = self.fixture(root)
            current['artifact_hashes']['datasystem/output/arbitrary.so'] = '0' * 64
            current['artifact_sizes']['datasystem/output/arbitrary.so'] = 1
            with self.assertRaises(ValueError):
                record(root, original, current)


if __name__ == '__main__':
    unittest.main()
