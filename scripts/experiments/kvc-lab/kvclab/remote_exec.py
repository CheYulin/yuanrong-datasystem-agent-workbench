"""Place a new single-threaded child in its admitted cgroup before exec."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    group = Path(args.group)
    if not group.is_absolute() or any(p.is_symlink() for p in [group, *group.parents]) or not command:
        parser.error("An absolute non-symlink cgroup and command are required")
    (group / "cgroup.procs").write_text(str(os.getpid()))
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
