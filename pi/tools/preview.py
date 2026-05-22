#!/usr/bin/env python3
"""
Off-Pi development tool: render a fixture to a PNG.

  python tools/preview.py                       # default fixture, opens result
  python tools/preview.py --data fixtures/first_run.json
  python tools/preview.py --update-golden       # regenerate every tests/golden/*.png
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# When run from pi/, add the parent so `import state` etc. work.
HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE.parent))

from renderer import render
from state import snapshot_from_payload

PI_DIR        = HERE.parent
FIXTURES_DIR  = HERE / "fixtures"
GOLDEN_DIR    = PI_DIR / "tests" / "golden"


def render_fixture(fixture_path: Path):
    data = json.loads(fixture_path.read_text())
    snapshot = snapshot_from_payload(data["payload"], now=data["now"])
    return render(snapshot)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=FIXTURES_DIR / "default.json",
                        help="path to a fixture JSON file")
    parser.add_argument("--out",  type=Path, default=Path("preview.png"),
                        help="output PNG path (default: ./preview.png in cwd)")
    parser.add_argument("--show", action="store_true",
                        help="open the result with xdg-open / open after writing")
    parser.add_argument("--update-golden", action="store_true",
                        help="render every fixture into tests/golden/")
    args = parser.parse_args(argv)

    if args.update_golden:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for fx in sorted(FIXTURES_DIR.glob("*.json")):
            img = render_fixture(fx)
            out = GOLDEN_DIR / f"{fx.stem}.png"
            img.save(out)
            print(f"wrote {out}")
        return 0

    img = render_fixture(args.data)
    img.save(args.out)
    print(f"wrote {args.out}")
    if args.show:
        opener = "xdg-open" if sys.platform.startswith("linux") else "open"
        subprocess.run([opener, str(args.out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
