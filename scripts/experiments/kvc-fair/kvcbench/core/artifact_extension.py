"""One explicit extension of the original DataSystem build inventory."""

COORDINATOR = 'datasystem/output/datasystem/service/datasystem_coordinator'


def validate(original, current):
    for key in ('product', 'sha', 'pinned_sha', 'tag_sha', 'tag', 'build_exit',
                'build_started_wall_ns', 'build_finished_wall_ns', 'build_recipe', 'build_recipe_sha256'):
        if original.get(key) != current.get(key):
            raise ValueError('Additional native artifact must retain the actual original build identity and interval')
    old, new = original['artifact_hashes'], current['artifact_hashes']
    if current['product'] != 'datasystem' or set(new) - set(old) != {COORDINATOR} or set(old) - set(new):
        raise ValueError('Only the existing Coordinator binary can extend the original inventory')
    if any(new[path] != value or original['artifact_sizes'][path] != current['artifact_sizes'][path]
           for path, value in old.items()):
        raise ValueError('Original build output bytes changed during Coordinator adoption')
