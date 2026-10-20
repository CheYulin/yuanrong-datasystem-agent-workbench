"""Reproduction archive from the explicit complete source inventory."""
from pathlib import Path
import zipfile


def reproduction_archive(scripts, rfc, out):
    archive = out / "reproduce.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        if (scripts / "bundle-inputs.json").is_file():
            from kvcbench.platform.package import inventory
            for name in inventory(scripts):
                zipped.write(scripts / name, "kvc-fair/" + name)
        else:
            for path in sorted(scripts.rglob("*")):
                relative = path.relative_to(scripts)
                if len(relative.parts) > 1 and relative.parts[0] not in {"datasystem", "mooncake", "lmcache"}:
                    continue
                if path.is_file() and not any(part.startswith(".") or part in {"source","evidence","__pycache__","venv","venv311"} for part in relative.parts) and path.suffix in {".py",".sh",".mjs",".js",".css",".html",".lock",".md",".json"}:
                    zipped.write(path, "kvc-fair/" + str(relative))
        for path in sorted(rfc.glob("*.md")):
            zipped.write(path, "kvc-fair/docs/" + path.name)
    return archive
