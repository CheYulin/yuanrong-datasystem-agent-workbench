"""Attest an additional Coordinator output against the original bound build tar."""
from pathlib import Path

from kvcbench.core.artifact_extension import COORDINATOR, validate
from kvcbench.platform.archives import verify_tar_inputs
from kvcbench.platform.files import identity, read_json, regular


def record(root, original, current):
    validate(original, current)
    archives = [name for name in original['artifact_hashes'] if name.endswith('.tar.gz')]
    if len(archives) != 1:
        raise ValueError('Coordinator adoption requires exactly one originally bound source-build tar')
    artifact = identity(regular(root, COORDINATOR))
    proof = {'schema_version': 1, 'original_metadata': 'build-metadata/datasystem.json',
             'original_identity': identity(regular(root, 'build-metadata/datasystem.json')),
             'archive': archives[0], 'archive_member': 'datasystem/service/datasystem_coordinator',
             'artifact': COORDINATOR, 'identity': artifact,
             'scope': 'Inventory extension only; exact member of the originally hashed build tar; no compilation in this attempt.'}
    current = dict(current, artifact_adoption=proof)
    verify(root, current)
    return current


def verify(root, current):
    proof = current['artifact_adoption']
    if (proof.get('schema_version') != 1 or proof.get('original_metadata') != 'build-metadata/datasystem.json'
            or proof.get('artifact') != COORDINATOR or proof.get('archive_member') != 'datasystem/service/datasystem_coordinator'):
        raise ValueError('Unknown build inventory extension')
    original_path = regular(root, proof['original_metadata'])
    if identity(original_path) != proof['original_identity']:
        raise ValueError('Original build metadata changed before Coordinator adoption')
    original = read_json(original_path)
    validate(original, current)
    archive = regular(root, proof['archive'])
    if (identity(archive)['sha256'] != original['artifact_hashes'].get(proof['archive'])
            or archive.stat().st_size != original['artifact_sizes'].get(proof['archive'])
            or identity(regular(root, COORDINATOR)) != proof['identity']
            or current['artifact_hashes'][COORDINATOR] != proof['identity']['sha256']
            or current['artifact_sizes'][COORDINATOR] != proof['identity']['bytes']):
        raise ValueError('Coordinator or original tar identity differs from the build evidence')
    verify_tar_inputs(archive, {proof['archive_member']: proof['identity']})
    return proof
