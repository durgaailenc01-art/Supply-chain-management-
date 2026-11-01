"""Generate twice in temporary directories and compare every CSV checksum."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .generate import SCALES, generate


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=SCALES, default="starter")
    args = parser.parse_args()
    workspace_tmp = Path("data").resolve()
    workspace_tmp.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=workspace_tmp) as first, TemporaryDirectory(dir=workspace_tmp) as second:
        one = generate(args.scale, Path(first))
        two = generate(args.scale, Path(second))
        matches = one["tables"] == two["tables"]
    print(json.dumps({"scale": args.scale, "seed": one["seed"], "matching_counts_and_checksums": matches}, indent=2))
    raise SystemExit(0 if matches else 1)
