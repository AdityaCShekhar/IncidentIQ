"""Initialize ownership of the dedicated local telemetry volume before the Collector starts."""

import os
from pathlib import Path


def main() -> None:
    directory = Path("/telemetry")
    # Existing data from the first stack revision was written by UID 0.
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 256:
                raise RuntimeError("Unexpected telemetry volume size")
            if entry.is_file(follow_symlinks=False):
                os.chown(entry.path, 10001, 10001, follow_symlinks=False)
    os.chown(directory, 10001, 10001, follow_symlinks=False)


if __name__ == "__main__":
    main()
