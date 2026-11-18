#!/usr/bin/env python3
"""Update the scientific icon library page after adding .ai files.

Usage:
  python3 scripts/update_icon_library.py
  python3 scripts/update_icon_library.py --force-thumbs

Put new Adobe Illustrator files under:
  resources/icon_library/<category>/<icon_name>.ai

The script scans those folders, refreshes missing/outdated PNG thumbnails in
resources/icon_library_thumbs/, and updates the categories array in
resources/icon_library.html.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "resources" / "icon_library"
THUMB_DIR = ROOT / "resources" / "icon_library_thumbs"
PAGE = ROOT / "resources" / "icon_library.html"

CATEGORY_TITLES = {
    "human": "Human",
    "species": "Species",
    "tissues_and_organs": "Tissues and Organs",
    "cell_types": "Cell Types",
    "cell_structures": "Cell Structures",
    "macromolecules": "Macromolecules",
    "lab_and_objects": "Lab and Objects",
    "technologies": "Technologies",
    "graphs_and_symbols": "Graphs and Symbols",
    "schematic_diagram": "Schematic Diagram",
}

CATEGORY_ORDER = [
    "human",
    "species",
    "tissues_and_organs",
    "cell_types",
    "cell_structures",
    "macromolecules",
    "lab_and_objects",
    "technologies",
    "graphs_and_symbols",
    "schematic_diagram",
]


def title_case(category_id: str) -> str:
    return CATEGORY_TITLES.get(
        category_id,
        " ".join(word.capitalize() for word in category_id.split("_")),
    )


def read_existing_order() -> tuple[list[str], dict[str, list[str]]]:
    if not PAGE.exists():
        return [], {}

    text = PAGE.read_text(encoding="utf-8")
    match = re.search(
        r"const categories = (?P<json>\[[\s\S]*?\]);\n\n\s+const labels =",
        text,
    )
    if not match:
        return [], {}

    try:
        categories = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return [], {}

    category_order = []
    icon_order = {}
    for category in categories:
        category_id = category.get("id")
        icons = category.get("icons", [])
        if isinstance(category_id, str) and isinstance(icons, list):
            category_order.append(category_id)
            icon_order[category_id] = [icon for icon in icons if isinstance(icon, str)]
    return category_order, icon_order


def scan_icons() -> dict[str, list[str]]:
    if not ICON_DIR.exists():
        raise SystemExit(f"Missing icon directory: {ICON_DIR}")

    categories: dict[str, list[str]] = {}
    for category_dir in sorted(path for path in ICON_DIR.iterdir() if path.is_dir()):
        icons = sorted(path.stem for path in category_dir.glob("*.ai"))
        categories[category_dir.name] = icons
    for category_id in CATEGORY_ORDER:
        categories.setdefault(category_id, [])
    return categories


def merge_order(
    scanned: dict[str, list[str]],
    existing_categories: list[str],
    existing_icons: dict[str, list[str]],
) -> list[dict[str, object]]:
    preferred_categories = []
    for category_id in [*CATEGORY_ORDER, *existing_categories, *sorted(scanned)]:
        if category_id in scanned and category_id not in preferred_categories:
            preferred_categories.append(category_id)

    merged = []
    for category_id in preferred_categories:
        available = set(scanned[category_id])
        ordered_icons = []
        for icon in existing_icons.get(category_id, []):
            if icon in available and icon not in ordered_icons:
                ordered_icons.append(icon)

        new_icons = available - set(ordered_icons)
        new_icons = sorted(
            new_icons,
            key=lambda icon: (
                (ICON_DIR / category_id / f"{icon}.ai").stat().st_mtime,
                icon,
            ),
        )
        for icon in new_icons:
            if icon not in ordered_icons:
                ordered_icons.append(icon)

        merged.append(
            {
                "id": category_id,
                "title": title_case(category_id),
                "icons": ordered_icons,
            }
        )
    return merged


def needs_thumbnail(ai_path: Path, thumb_path: Path, force: bool) -> bool:
    if force or not thumb_path.exists():
        return True
    return ai_path.stat().st_mtime > thumb_path.stat().st_mtime


def generate_thumbnail(ai_path: Path, thumb_path: Path) -> None:
    qlmanage = shutil.which("qlmanage")
    if qlmanage is None:
        raise RuntimeError("qlmanage was not found. Thumbnail generation requires macOS.")

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        subprocess.run(
            [qlmanage, "-t", "-s", "512", "-o", str(tmp_dir), str(ai_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        generated = tmp_dir / f"{ai_path.name}.png"
        if not generated.exists():
            raise RuntimeError(f"Quick Look did not create a thumbnail for {ai_path}")
        shutil.move(str(generated), thumb_path)


def refresh_thumbnails(scanned: dict[str, list[str]], force: bool) -> tuple[int, list[str]]:
    generated = 0
    failures = []
    for category_id, icons in scanned.items():
        for icon in icons:
            ai_path = ICON_DIR / category_id / f"{icon}.ai"
            thumb_path = THUMB_DIR / category_id / f"{icon}.png"
            if not needs_thumbnail(ai_path, thumb_path, force):
                continue
            try:
                generate_thumbnail(ai_path, thumb_path)
                generated += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{ai_path.relative_to(ROOT)}: {exc}")
    return generated, failures


def update_page(categories: list[dict[str, object]]) -> None:
    if not PAGE.exists():
        raise SystemExit(f"Missing page: {PAGE}")

    text = PAGE.read_text(encoding="utf-8")
    categories_json = json.dumps(categories, indent=6, ensure_ascii=False)
    categories_json = re.sub(r"^", "    ", categories_json, flags=re.MULTILINE)
    replacement = f"const categories = {categories_json.strip()};\n\n    const labels ="

    updated, count = re.subn(
        r"const categories = \[[\s\S]*?\];\n\n\s+const labels =",
        replacement,
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not find the categories block in resources/icon_library.html")
    PAGE.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh icon library thumbnails and page data.",
    )
    parser.add_argument(
        "--force-thumbs",
        action="store_true",
        help="Regenerate all thumbnails even when they already exist.",
    )
    args = parser.parse_args()

    existing_categories, existing_icons = read_existing_order()
    scanned = scan_icons()
    categories = merge_order(scanned, existing_categories, existing_icons)
    generated, failures = refresh_thumbnails(scanned, args.force_thumbs)
    update_page(categories)

    icon_count = sum(len(category["icons"]) for category in categories)
    print(f"Updated {PAGE.relative_to(ROOT)}")
    print(f"Found {icon_count} .ai icons in {len(categories)} categories")
    print(f"Generated {generated} thumbnails")
    if failures:
        print("\nThumbnail failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
