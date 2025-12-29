from pathlib import Path

import aiarena.utils.waste_highlighter as wh


def test_waste_highlighter_contains_no_destructive_calls():
    src_path = Path(wh.__file__).resolve()
    text = src_path.read_text(encoding="utf-8", errors="ignore")

    # Only ban genuinely destructive filesystem calls (not string replace()).
    banned = [
        "os.remove(",
        "os.unlink(",
        "os.replace(",
        "shutil.rmtree(",
        "shutil.move(",
        "shutil.copytree(",
        "Path.unlink(",
        "Path.rename(",
        "Path.replace(",
    ]

    hits = [b for b in banned if b in text]
    assert not hits, f"Destructive call(s) detected in waste_highlighter.py: {hits}"
