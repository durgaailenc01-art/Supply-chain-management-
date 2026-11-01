import argparse
import json
from pathlib import Path

from .engine import optimize_dataset

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/starter"))
    args = parser.parse_args()
    print(json.dumps(optimize_dataset(args.dataset), indent=2))
