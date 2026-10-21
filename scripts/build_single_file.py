#!/usr/bin/env python3
"""src/simcompare 전체와 순수 Python PyYAML을 .py 파일 하나로 묶는다. 명령줄 옵션을 넘길 수 없고 .py만 올릴 수 있는 서버용.

만들어진 파일 맨 위의 EDIT 블록(JOBS, LOG_FILE, WORK_DIR, BASE_DIR)만 고치면 된다. 실행하면 패키지를 실제 폴더에 풀고(바이트 그대로)
simcompare.cli.main(인자)을 순서대로 호출한다. 서버에는 numpy와 Pillow만 있으면 된다(PyYAML은 묶음에 들어 있다)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import zlib

import yaml

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = '''#!/usr/bin/env python3
"""simcompare 단일 파일 실행기 (자동 생성: scripts/build_single_file.py). 소스 해시 @@SHA@@.
서버 wrapper가 명령줄 인자를 넘기지 못할 때 쓴다. 아래 EDIT 블록만 고치고 이 파일을 그대로 실행한다."""
# ============================== EDIT BELOW: 이 블록만 고치세요 ==============================
# 각 작업은 `python -m simcompare <명령> <인자...>`와 같다. 명령 이름은 import-hotspots, validate-data, compare,
# electrode-map, import-electrode-assets, export-review-rois, candidates, confirm-pairs, compare-windows, report 등이다.
# 인자는 명령줄에서 쓰던 그대로 문자열 리스트로 적는다. 상대 경로는 BASE_DIR 기준이다(아래). wrapper가 이 파일을 다른 곳에
# 복사해 실행하면 "이 .py가 있는 폴더"가 달라지므로, 상대 경로를 쓸 때는 BASE_DIR를 절대 경로로 적는 것이 안전하다.
# 작업은 위에서부터 차례로 실행된다. 종료 코드 0(완료)과 3(부분 결과)은 다음 작업으로 넘어가고, 그 밖의 코드는 멈춘다.
JOBS = [
    ("import-hotspots", ["--root", "/data/input/hotspot/QA4_R00", "--out", "/data/imported/reference"]),
    ("import-hotspots", ["--root", "/data/input/hotspot/QA4_T00", "--out", "/data/imported/target"]),
    # ("validate-data", ["--config", "/data/real.yaml"]),
    # ("compare", ["--config", "/data/real.yaml"]),
]
LOG_FILE = "/data/runs/simcompare_job.log"   # stdout/stderr를 이 파일에도 남긴다. 필요 없으면 None
WORK_DIR = None                               # 패키지를 풀 폴더. None이면 이 파일 옆, 쓸 수 없으면 임시 폴더
BASE_DIR = None                               # JOBS·LOG_FILE·WORK_DIR의 상대 경로 기준 폴더(절대 경로). None이면 이 .py가 있는 폴더
# ============================== EDIT ABOVE ==============================================

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
import zlib
from pathlib import Path

CONTINUE_CODES = (0, 3)
PAYLOAD_SHA256 = "@@SHA@@"
PAYLOAD = (
@@PAYLOAD@@
)


def _files():
    files = json.loads(zlib.decompress(base64.b64decode("".join(PAYLOAD))))
    for name, entry in files.items():
        data = base64.b64decode(entry["data"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"embedded file is corrupted: {name}")
        files[name] = data
    return files


def _extract():
    """simcompare와 yaml 패키지를 실제 폴더에 푼다(바이트 그대로). 이미 같은 내용이 있으면 재사용한다. 풀린 위치를 돌려준다."""
    files = _files()
    here = globals().get("__file__")
    bases = [Path(WORK_DIR)] if WORK_DIR else []
    bases += ([Path(here).resolve().parent] if here else [Path.cwd()]) + [Path(tempfile.gettempdir())]
    errors = []
    for base in bases:
        root = base / f".simcompare_pkg_{PAYLOAD_SHA256[:12]}"
        try:
            for name, data in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file() or target.read_bytes() != data:
                    target.write_bytes(data)
            return root
        except OSError as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeError("cannot extract the simcompare package anywhere: " + "; ".join(errors))


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return False


def run_jobs(jobs):
    root = _extract()
    sys.path.insert(0, str(root))
    import simcompare
    import yaml
    for module in (simcompare, yaml):
        if not str(Path(module.__file__).resolve()).startswith(str(root.resolve())):
            raise RuntimeError(f"another {module.__name__} shadows the bundled one: {module.__file__}")
    from simcompare.cli import main
    stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
    import numpy
    import PIL
    print(f"[simcompare_job] {stamp()} python {sys.version.split()[0]} | numpy {numpy.__version__} | Pillow {PIL.__version__}"
          f" | simcompare from {simcompare.__file__}", file=sys.stderr, flush=True)
    for name, args in jobs:
        argv = [str(name), *(str(a) for a in args)]
        print(f"[simcompare_job] {stamp()} start {' '.join(argv)}", file=sys.stderr, flush=True)
        started = time.time()
        sys.argv = ["simcompare", *argv]
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse 오류 등
            code = exc.code if isinstance(exc.code, int) else 2
        code = 0 if code is None else code
        print(f"[simcompare_job] {stamp()} {name} exit {code} in {time.time() - started:.0f}s", file=sys.stderr, flush=True)
        if code not in CONTINUE_CODES:
            raise SystemExit(code)


def _base_dir():
    """상대 경로 기준 폴더: BASE_DIR, 없으면 이 파일의 폴더, __file__도 없으면 현재 작업 폴더."""
    if BASE_DIR:
        return Path(BASE_DIR).expanduser().resolve(), "BASE_DIR"
    here = globals().get("__file__")
    if here:
        return Path(here).resolve().parent, "runner file folder"
    return Path.cwd(), "current working directory (__file__ unavailable)"


def _main():
    base, source = _base_dir()
    launched_cwd = os.getcwd()
    os.chdir(base)
    log = None
    if LOG_FILE:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
        sys.stdout, sys.stderr = _Tee(sys.__stdout__, log), _Tee(sys.__stderr__, log)
    print(f"[simcompare_job] relative paths resolve from {base} ({source}); launched in {launched_cwd};"
          f" runner {globals().get('__file__')}", file=sys.stderr, flush=True)
    try:
        run_jobs(JOBS)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        raise
    finally:
        if log is not None:
            log.flush()


if __name__ == "__main__":
    _main()
'''


def _sources():
    """묶을 파일: src/simcompare 아래 모든 .py와 설치된 PyYAML의 순수 Python 모듈(C 확장 cyaml 제외)."""
    package = ROOT / "src"
    files = {p.relative_to(package).as_posix(): p for p in sorted((package / "simcompare").rglob("*.py"))
             if "__pycache__" not in p.parts}
    yaml_dir = Path(yaml.__file__).resolve().parent
    for p in sorted(yaml_dir.glob("*.py")):
        if p.name != "cyaml.py":
            files[f"yaml/{p.name}"] = p
    return files


def _jobs_block(jobs):
    """JOBS 리스트를 EDIT 블록에 넣을 파이썬 소스로 만든다. jobs는 (명령, [인자...], 주석 또는 None) 목록이다."""
    lines = ["JOBS = ["]
    for name, args, comment in jobs:
        if comment:
            lines.append(f"    # {comment}")
        lines.append(f"    ({name!r}, [")
        index = 0
        while index < len(args):
            # "--옵션 값"은 한 줄에 둔다.
            pair = (args[index].startswith("--") and index + 1 < len(args) and not args[index + 1].startswith("--"))
            chunk = args[index:index + 2] if pair else args[index:index + 1]
            lines.append("        " + " ".join(f"{a!r}," for a in chunk))
            index += len(chunk)
        lines.append("    ]),")
    lines.append("]")
    return "\n".join(lines) + "\n"


def build(out, *, overwrite=False, jobs=None, log_file=None, header=None):
    out = Path(out)
    if out.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {out}; pass --overwrite or choose a new path")
    sources = _sources()
    if not any(name.startswith("simcompare/") for name in sources):
        raise ValueError("no source files found under src/simcompare")
    files = {name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "data": base64.b64encode(p.read_bytes()).decode()}
             for name, p in sources.items()}
    packed = base64.b64encode(zlib.compress(json.dumps(files, sort_keys=True).encode(), 9)).decode()
    sha = hashlib.sha256(json.dumps({n: e["sha256"] for n, e in files.items()}, sort_keys=True).encode()).hexdigest()
    lines = "\n".join(f'    "{packed[i:i + 100]}"' for i in range(0, len(packed), 100))
    text = TEMPLATE.replace("@@SHA@@", sha).replace("@@PAYLOAD@@", lines)
    if jobs is not None:
        start, end = text.index("JOBS = ["), text.index("LOG_FILE = ")
        text = text[:start] + _jobs_block(jobs) + text[end:]
    if log_file is not None:
        start = text.index("LOG_FILE = ")
        end = text.index("\n", start)
        text = text[:start] + f"LOG_FILE = {log_file!r}   # stdout/stderr를 이 파일에도 남긴다. 필요 없으면 None" + text[end:]
    if header:
        marker = "# ============================== EDIT BELOW"
        text = text.replace(marker, "".join(f"# {line}\n" for line in header) + marker, 1)
    compile(text, str(out), "exec")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return {"out": str(out), "bytes": out.stat().st_size, "modules": len(files), "yaml_version": yaml.__version__,
            "source_sha256": sha}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="single .py file to write, e.g. bundles/simcompare_job.py")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.out, overwrite=a.overwrite), indent=2))


if __name__ == "__main__":
    main()
