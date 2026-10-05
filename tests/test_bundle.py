import stat
from pathlib import Path

import pytest

from fsbench.bundle import BundleError, materialise, parse, render

ROOT = Path(__file__).resolve().parent.parent


def test_parse_and_materialise(tmp_path):
    text = ("prose the model added\n=== FILE: instruction.md ===\n# Do it\n=== FILE: solution/solve.sh ===\n"
            "#!/bin/bash\necho hi\n=== FILE: environment/repo/a.py ===\nx = 1\n=== END ===\ntrailing junk")
    files = parse(text)
    assert files == {"instruction.md": "# Do it\n", "solution/solve.sh": "#!/bin/bash\necho hi\n",
                     "environment/repo/a.py": "x = 1\n"}
    materialise(files, tmp_path / "t")
    assert (tmp_path / "t/environment/repo/a.py").read_text() == "x = 1\n"
    assert stat.S_IMODE((tmp_path / "t/solution/solve.sh").stat().st_mode) == 0o755


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.txt", "environment/../../x", "README.md", ".github/x"])
def test_unsafe_paths_rejected(tmp_path, path):
    with pytest.raises(BundleError):
        materialise({path: "x"}, tmp_path / "t")


def test_symlinks_not_followed(tmp_path):
    (tmp_path / "t" / "environment").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "t" / "environment" / "repo").symlink_to(tmp_path / "outside")
    with pytest.raises(BundleError, match="symlink"):
        materialise({"environment/repo/x.py": "1"}, tmp_path / "t")
    assert not (tmp_path / "outside" / "x.py").exists()


def test_no_headers_is_an_error():
    with pytest.raises(BundleError):
        parse("just prose")


def test_render_roundtrip_of_an_exemplar(tmp_path):
    text = render(ROOT / "tasks" / "ship-checkout-v2")
    files = parse(text)
    assert "instruction.md" in files and "environment/repo/shopsrv.py" in files
    assert not any("/skills/" in p for p in files)
    materialise(files, tmp_path / "copy")
    assert (tmp_path / "copy/environment/repo/shopsrv.py").read_text() == \
        (ROOT / "tasks/ship-checkout-v2/environment/repo/shopsrv.py").read_text()
