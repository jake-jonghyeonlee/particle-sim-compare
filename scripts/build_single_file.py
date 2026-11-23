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
# electrode-map, import-electrode-assets, export-review-rois, candidates-change, candidates, confirm-pairs, compare-windows, report 등이다.
# 인자는 명령줄에서 쓰던 그대로 문자열 리스트로 적는다. 상대 경로는 BASE_DIR 기준이다(아래). wrapper가 이 파일을 다른 곳에
# 복사해 실행하면 "이 .py가 있는 폴더"가 달라지므로, 상대 경로를 쓸 때는 BASE_DIR를 절대 경로로 적는 것이 안전하다.
# 작업은 위에서부터 차례로 실행된다. 종료 코드 0(완료)과 3(부분 결과)은 다음 작업으로 넘어가고, 그 밖의 코드는 멈춘다.
JOBS = [
    ("import-hotspots", ["--root", "data/input/QA4_R00", "--out", "data/imported/reference"]),
    ("import-hotspots", ["--root", "data/input/QA4_T00", "--out", "data/imported/target"]),
    # ("validate-data", ["--config", "real.yaml"]),
    # ("compare", ["--config", "real.yaml"]),
]
LOG_FILE = "runs/simcompare_job.log"          # stdout/stderr를 이 파일에도 남긴다. 필요 없으면 None
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


EXTRACT_FALLBACKS = []   # (시도한 폴더, 예외 문자열): 앞선 폴더에 풀지 못해 다음 폴더로 넘어간 이유


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
            EXTRACT_FALLBACKS.append((str(base), str(exc)))
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


PATH_FLAGS = {"--root", "--out", "--config", "--expected-cases", "--expected-cases-from-roots", "--numeric-data-dir",
              "--condition-assignment", "--cnt-from-dl1-root", "--candidates", "--decisions", "--match",
              "--correspondence", "--windows", "--confirm", "--dl1-search", "--compare2-dir"}


KEY_PATH_FLAGS = {"confirm-pairs": ("--match",), "model-report": ("--windows",)}     # KEY=PATH 형식 옵션(명령별)


def _resolved_args(args, command=None):
    """경로를 받는 옵션마다 `옵션 원문 -> 해석된 절대 경로`를 만든다(상대 경로는 이미 chdir한 BASE_DIR 기준).
    KEY=PATH 형식은 명령별 옵션만 `=` 뒤를 경로로 보고, 다른 명령(compare2-report --windows DIR 등)은 값 전체가 경로다."""
    lines, flag = [], None
    for arg in (str(a) for a in args):
        if arg.startswith("--"):
            flag = arg if arg in PATH_FLAGS else None
        elif flag is not None:
            value = arg.split("=", 1)[1] if flag in KEY_PATH_FLAGS.get(str(command), ()) and "=" in arg else arg
            lines.append(f"{flag} {arg!r} -> {os.path.abspath(os.path.expanduser(value))}")
    return lines


def _startup_lines(base, source, launched_cwd):
    """실행 환경을 한 번에 보여 주는 시작 정보. 로그 파일을 못 열어도 stderr에 같은 내용이 남는다."""
    return [f"[simcompare_job] runner {globals().get('__file__')}",
            f"[simcompare_job] payload_sha256={PAYLOAD_SHA256}",
            f"[simcompare_job] BASE_DIR requested={BASE_DIR!r} resolved={base}; relative paths resolve from {base} ({source});"
            f" launched in {launched_cwd}",
            f"[simcompare_job] LOG_FILE requested={LOG_FILE!r}; WORK_DIR requested={WORK_DIR!r}"]


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
    print(f"[simcompare_job] payload_sha256={PAYLOAD_SHA256}", file=sys.stderr, flush=True)
    for fallback_base, reason in EXTRACT_FALLBACKS:
        print(f"[simcompare_job] cannot extract into {fallback_base}: {reason}; trying the next folder",
              file=sys.stderr, flush=True)
    print(f"[simcompare_job] WORK_DIR requested={WORK_DIR!r}; package extracted to {root}", file=sys.stderr, flush=True)
    if callable(jobs):      # 통합 실행기: 패키지를 푼 뒤 설정 파일에서 JOBS 를 만든다
        jobs = jobs()
    # job 은 (명령, 인자) 2-tuple, 또는 단계가 붙은 것: (단계, 명령, 인자) 3-tuple / .step·.label 이 있는 통합 실행기 job.
    plan = []       # (단계 또는 None, 단계 이름표, 명령, 인자)
    for job in jobs:
        if len(job) == 3:
            plan.append((job[0], job[0], job[1], job[2]))
        else:
            step = getattr(job, "step", None)
            plan.append((step, getattr(job, "label", step), job[0], job[1]))
    step_ids = []
    for step, _, _, _ in plan:
        if step is not None and step not in step_ids:
            step_ids.append(step)
    labels = {step: label for step, label, _, _ in plan if step is not None}
    counts = {step: sum(1 for item in plan if item[0] == step) for step in step_ids}
    results = {}        # 단계 -> [시작 시각, [(명령, exit)...], 끝 시각 또는 None]

    def fmt(seconds):
        seconds = int(round(seconds))
        return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"

    def worst(codes):       # 멈추는 코드 > 3 > 0
        stops = [c for c in codes if c not in CONTINUE_CODES]
        return max(stops) if stops else (3 if 3 in codes else 0)

    def say(text):
        print(f"[simcompare_job] {text}", file=sys.stderr, flush=True)

    def close_step(step, failed=False):
        started_at, done, _ = results[step]
        elapsed = time.time() - started_at
        results[step][2] = elapsed
        if failed:
            say(f"===== [{step}] 실패 · {done[-1][0]} exit {done[-1][1]} · 이후 단계는 실행하지 않음 =====")
        else:
            say(f"===== [{step}] 끝 {stamp()} · {fmt(elapsed)} · exit " + ", ".join(f"{n} {c}" for n, c in done) + " =====")

    def summary():
        if not step_ids:
            return
        say("요약")
        for step in step_ids:
            if step not in results:
                say(f"  [{step}] -  (실행 안 함)")
            else:
                started_at, done, elapsed = results[step]
                say(f"  [{step}] {worst([c for _, c in done])}  {fmt(time.time() - started_at if elapsed is None else elapsed)}")
        say(f"  전체 {fmt(time.time() - run_started)}")

    run_started = time.time()
    current = None
    for index, (step, label, name, args) in enumerate(plan):
        argv = [str(name), *(str(a) for a in args)]
        if step is not None and step != current:
            if current is not None:
                close_step(current)
            current = step
            results[step] = [time.time(), [], None]
            say(f"===== [{step}] {label} 시작 {stamp()} (job {counts[step]} 개) =====")
        print(f"[simcompare_job] {stamp()} start {' '.join(argv)}", file=sys.stderr, flush=True)
        for line in _resolved_args(args, name):
            print(f"[simcompare_job]   {line}", file=sys.stderr, flush=True)
        started = time.time()
        sys.argv = ["simcompare", *argv]
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse 오류 등
            code = exc.code if isinstance(exc.code, int) else 2
        code = 0 if code is None else code
        print(f"[simcompare_job] {stamp()} {name} exit {code} in {time.time() - started:.0f}s", file=sys.stderr, flush=True)
        if step is not None:
            results[step][1].append((str(name), code))
        if code not in CONTINUE_CODES:
            if step is not None:
                close_step(step, failed=True)
                summary()
            raise SystemExit(code)
        if step is not None and (index + 1 == len(plan) or plan[index + 1][0] != step):
            close_step(step)
            current = None
    summary()


def _base_dir():
    """상대 경로 기준 폴더: BASE_DIR, 없으면 이 파일의 폴더, __file__도 없으면 현재 작업 폴더."""
    if BASE_DIR:
        return Path(BASE_DIR).expanduser().resolve(), "BASE_DIR"
    here = globals().get("__file__")
    if here:
        return Path(here).resolve().parent, "runner file folder"
    return Path.cwd(), "current working directory (__file__ unavailable)"


def _main():
    # 화면 인코딩(EUC-KR 등)에 없는 글자(µ 등)가 있어도 죽지 않도록, 첫 출력 전에 오류 처리만 바꾼다(인코딩은 그대로).
    for stream in (sys.__stdout__, sys.__stderr__):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass
    # 시작 정보는 BASE_DIR 처리와 로그 파일 열기보다 먼저 stderr로 낸다(그 둘이 실패해도 남는다).
    for line in (f"[simcompare_job] runner {globals().get('__file__')}", f"[simcompare_job] payload_sha256={PAYLOAD_SHA256}",
                 f"[simcompare_job] BASE_DIR requested={BASE_DIR!r}; LOG_FILE requested={LOG_FILE!r}; WORK_DIR requested={WORK_DIR!r}"):
        print(line, file=sys.stderr, flush=True)
    try:
        base, source = _base_dir()
        launched_cwd = os.getcwd()
        os.chdir(base)
    except OSError as exc:
        print(f"[simcompare_job] cannot use BASE_DIR {BASE_DIR!r}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    log = None
    if LOG_FILE:
        try:
            Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
            log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
        except OSError as exc:
            print(f"[simcompare_job] cannot open LOG_FILE {LOG_FILE!r} (resolved {os.path.abspath(LOG_FILE)}): {exc}",
                  file=sys.stderr, flush=True)
            raise SystemExit(2)
        sys.stdout, sys.stderr = _Tee(sys.__stdout__, log), _Tee(sys.__stderr__, log)
    for line in _startup_lines(base, source, launched_cwd):
        print(line, file=sys.stderr, flush=True)
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


def build(out, *, overwrite=False, jobs=None, log_file=None, header=None, flow=None):
    """flow = (EDIT 블록 본문, 함수 본문): 통합 실행기. EDIT 블록 본문이 JOBS·LOG_FILE·WORK_DIR·BASE_DIR 자리를 대신하고,
    함수 본문(`_flow_jobs`)이 run_jobs 에 넘겨져 패키지를 푼 뒤 JOBS 를 만든다."""
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
    if flow is not None:
        edit_text, func_text = flow
        start = text.index("# ============================== EDIT BELOW")
        start = text.index("\n", start) + 1
        end = text.index("# ============================== EDIT ABOVE")
        text = text[:start] + edit_text + text[end:]
        text = text.replace("        run_jobs(JOBS)\n", "        run_jobs(_flow_jobs)\n", 1)
        text = text.replace("def _main():", func_text + "\n\ndef _main():", 1)
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
