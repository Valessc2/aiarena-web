from pathlib import Path

import aiarena.utils.waste_highlighter as wh


def test_waste_highlighter_contains_no_destructive_calls():
    src_path = Path(wh.__file__).resolve()
    text = src_path.read_text(encoding="utf-8", errors="ignore")

    banned = [
        "os.remove",
        "os.unlink",
        "shutil.rmtree",
        "shutil.move",
        "Path.unlink",
        "Path.rename",
        "Path.replace",
        ".unlink(",
        ".rename(",
        ".replace(",
        "rmtree(",
        "unlink(",
    ]

    hits = [b for b in banned if b in text]
    assert not hits, f"Destructive call(s) detected in waste_highlighter.py: {hits}"
