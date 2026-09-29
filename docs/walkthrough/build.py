"""따라하기 문서(docs/walkthrough.pdf)를 처음부터 다시 만든다.

1) 임시 폴더에서 더미 데이터 생성 → 점검 → 학습 → 요약 좌표 → 분석 → 평가를 실제로 실행하고
2) 결과 파일로 그림(figures/*.svg)을 그린 뒤
3) 결과 숫자를 넣은 HTML(walkthrough.html)을 만들고 Chrome으로 PDF를 인쇄한다.

실행(저장소 루트에서):
    uv run --with matplotlib python docs/walkthrough/build.py

matplotlib은 문서용이라 프로젝트 의존성에 넣지 않았다. PDF 변환에는 Google Chrome(또는 Chromium)이 필요하다.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import figures  # noqa: E402

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]
STEPS = ["validate-data", "train", "embed", "analyze", "evaluate"]


# ------------------------------------------------------------------ 실행
def run_pipeline(work: Path) -> dict:
    """저장소의 configs/demo.yaml을 그대로 쓰되 경로만 임시 폴더로 바꿔 전체 흐름을 실행한다."""
    cfg = yaml.safe_load((ROOT / "configs/demo.yaml").read_text())
    cfg["data"]["data_dir"] = str(work / "data")
    cfg["output_dir"] = str(work / "out")
    config = work / "demo.yaml"
    config.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))

    logs, seconds = {}, {}
    commands = [("demo-data", ["demo-data", "--out", str(work / "data"), "--seed", "0"])]
    commands += [(step, [step, "--config", str(config)]) for step in STEPS]
    for name, args in commands:
        start = time.perf_counter()
        proc = subprocess.run([sys.executable, "-m", "simcompare", *args], capture_output=True, text=True, cwd=ROOT)
        seconds[name] = time.perf_counter() - start
        if proc.returncode != 0:
            raise SystemExit(f"{name} 실패:\n{proc.stderr}")
        logs[name] = json.loads(proc.stdout)
        print(f"  {name}: {seconds[name]:.1f}s", file=sys.stderr)
    return {"logs": logs, "seconds": seconds}


# ------------------------------------------------------------------ HTML 도우미
def esc(x) -> str:
    return html.escape(str(x))


def code(cmd: str) -> str:
    return f'<pre class="cmd"><span class="prompt">$</span> {esc(cmd)}</pre>'


def json_excerpt(obj: dict, keys: list[str]) -> str:
    part = {k: obj[k] for k in keys if k in obj}
    return f'<pre class="out">{esc(json.dumps(part, ensure_ascii=False, indent=2))}</pre>'


def figure(name: str, caption: str, width: str = "100%") -> str:
    return (f'<figure><img src="figures/{name}" style="width:{width}">'
            f'<figcaption>{caption}</figcaption></figure>')


def table(head: list[str], rows: list[list], cls: str = "") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def note(text: str, kind: str = "info") -> str:
    return f'<div class="note {kind}">{text}</div>'


def tiles(items: list[tuple[str, str]]) -> str:
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="num">{esc(v)}</div><div class="lbl">{esc(k)}</div></div>' for k, v in items) + "</div>"


def short(sid: str) -> str:
    p, v, _ = sid.split("-")
    return f"{p} {v}" if p == "P1" else p


# ------------------------------------------------------------------ 본문
def build_html(work: Path, run: dict) -> str:
    out, data = work / "out", work / "data"
    logs, secs = run["logs"], run["seconds"]
    val = json.loads((out / "validation.json").read_text())
    split = json.loads((out / "split.json").read_text())
    hist = pd.read_csv(out / "train_history.csv")
    rank = pd.read_csv(out / "ranking.csv")
    agree = json.loads((out / "rank_agreement.json").read_text())
    nb = pd.read_csv(out / "neighbors.csv")
    loo = pd.read_csv(out / "loo_stability.csv")
    cards = json.loads((out / "review_cards.json").read_text())
    ev = json.loads((out / "evaluation.json").read_text())
    hood = pd.read_csv(out / "neighborhood_comparison.csv")
    hood_geo = pd.read_csv(out / "neighborhood_geometry.csv")
    edits = pd.read_csv(out / "edit_candidates.csv")
    variants = pd.read_csv(out / "variant_comparison.csv")
    total_sec = sum(secs.values())
    n_files = sum(1 for f in out.rglob("*") if f.is_file())
    auto_figs = sorted(f.name for f in (out / "figures").glob("*.svg"))
    crop_svg(out / "figures" / "rank_shift.svg", HERE / "figures" / "auto_rank_shift.svg", 350)

    train_products = sorted({s["sample_id"].split("-")[0] for s in split["train"]})
    test_products = sorted({s["sample_id"].split("-")[0] for s in split["test"]})
    pixel_warn = [w for w in val["warnings"] if "픽셀" in w]
    flagged = [i["sample_id"] for i in val["hotspot_layout_overlap"]["items"] if i["flagged"]]
    opens = rank[rank["failure_type"] == "lateral_open"].set_index("sample_id")
    p5_ratio = opens.loc["P5-v1-OPEN", "ca_max"] / opens.drop(index="P5-v1-OPEN")["ca_max"].median()
    agree_by = {a["failure_type"]: a for a in agree}
    ab = ev["ablation"]

    fl = {"lateral_open": "끊김", "lateral_short": "옆 전극 붙음", "vertical_short": "위아래 전극 붙음"}
    p = []  # 본문 조각

    # ---------------- 표지
    p.append(f"""
<section class="cover">
  <div class="kicker">simcompare 따라하기</div>
  <h1>더미 데이터로 처음부터<br>결과 읽기까지</h1>
  <p class="lead">가짜(더미) 데이터를 만들고, 점검·학습·분석·평가를 실제로 돌린 뒤
  나온 결과를 그림으로 읽는 과정을 순서대로 보여 줍니다. 이 문서의 모든 숫자와 그림은
  아래 명령을 실제로 실행해서 얻은 값입니다.</p>
  {tiles([("전체 실행 시간(CPU)", f"약 {total_sec:.0f}초"), ("만든 사례 수", str(val['counts']['n_bundles'])),
          ("비교한 제품 수", str(val['counts']['n_products'])), ("만들어진 결과 파일", f"{n_files}개")])}
  {note("<b>꼭 기억할 점</b> — 여기서 쓴 데이터는 간단한 수식으로 만든 <b>가짜</b>입니다. "
        "그림 속 결과는 “프로그램이 이렇게 동작한다”를 보여 줄 뿐, 실제 제품에 대한 결론이 아닙니다.", "warn")}
  <div class="toc">
    <div class="toc-title">차례</div>
    <ol>
      <li>이 도구는 무엇을 하나요</li>
      <li>전체 흐름 한눈에 보기</li>
      <li>1단계 · 더미 데이터 만들기</li>
      <li>2단계 · 데이터 점검</li>
      <li>3단계 · 모델 학습</li>
      <li>4단계 · 사례를 좌표로 바꾸기</li>
      <li>5단계 · 분석 (위험 비교 · 순위 · 비슷한 사례 · 검토 카드 · 회피안 후보)</li>
      <li>6단계 · 평가</li>
      <li>결과를 읽는 순서와 주의할 점</li>
      <li>내 데이터로 바꿔 실행하기</li>
    </ol>
  </div>
</section>""")

    # ---------------- 1. 무엇을 하나
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">1</span>이 도구는 무엇을 하나요</h2>
<p>디스플레이 공정에서는 아주 작은 먼지(<b>이물</b>)가 떨어져 전극이 끊어지거나 서로 붙는 불량이 생깁니다.
설계팀은 제품마다 “이물이 떨어지면 얼마나 위험한지”를 이미 시뮬레이션으로 계산해 둡니다.
<b>simcompare</b>는 이렇게 <b>이미 계산된 결과</b>를 여러 제품끼리 나란히 놓고 비교하는 도구입니다.</p>

<div class="two">
  <div class="box good"><div class="box-title">할 수 있는 것</div><ul>
    <li>여러 제품에서 반복되는 위험 유형 찾기</li>
    <li>“이 사례와 비슷한 다른 제품 사례” 찾기</li>
    <li>제품 크기·공장(Fab) 차이를 반영하면 위험 순위가 어떻게 바뀌는지 보기</li>
    <li>설계자가 검토할 근거를 한 장짜리 <b>검토 카드</b>로 정리</li></ul></div>
  <div class="box bad"><div class="box-title">하지 않는 것</div><ul>
    <li>새 설계의 위험(CA·hotspot)을 <b>예측</b>하기</li>
    <li>설계를 자동으로 고치기</li>
    <li>“이것이 원인이다”라고 <b>확정</b>하기</li>
    <li>수율·불량률 계산하기</li></ul></div>
</div>

<h3>먼저 알아 둘 용어 (9개면 충분합니다)</h3>
{table(["용어", "쉬운 설명"], [
    ["이물", "공정 중에 떨어지는 작은 먼지 입자. 크기(µm)별로 다룹니다."],
    ["layout (전극 그림)", "전극이 어떻게 생겼는지 보여 주는 흑백 그림."],
    ["hotspot", "전극 그림 위에서 <b>불량이 잘 나는 자리</b>를 진하게 표시한 그림. 이물 크기마다 한 장씩."],
    ["CA", "이물이 떨어졌을 때 불량으로 이어지는 정도를 <b>0~1 사이 값</b>으로 나타낸 위험값. 클수록 이물에 약한 설계입니다."],
    ["면적당 CA", "CA를 화소(unit-pixel) 한 칸의 면적으로 나눈 값. 화소가 큰 제품은 CA도 커지기 쉬워서, 크기가 다른 제품끼리 공정하게 비교할 때 씁니다."],
    ["Fab 분포", "실제 공장(Fab)에서 관측한 <b>이물 크기별 비율</b>. 공장마다 다릅니다."],
    ["CAA", "CA에 Fab 분포를 곱해 더한 값. “이 공장 기준으로 얼마나 취약한가”를 보는 <b>비교용 지표</b>이며 수율이 아닙니다."],
    ["사례", "제품 × 설계 버전 × 불량 종류 한 묶음. 예) <code>P1-v1-OPEN</code> = 제품 P1, 버전 v1, 끊김 불량."],
    ["요약 좌표", "사례 하나를 숫자 몇 개로 줄인 것. 좌표가 가까우면 비슷한 사례로 봅니다."],
], "terms")}

<p class="small">불량 종류는 세 가지입니다: <b>끊김</b>(lateral_open, 전극 1개),
<b>옆 전극 붙음</b>(lateral_short, 같은 층 전극 2개), <b>위아래 전극 붙음</b>(vertical_short, 다른 층 전극 2개).</p>
</section>""")

    # ---------------- 2. 흐름
    flow = [
        ("1", "더미 데이터", "demo-data", "demo_data/", "가짜 제품 6개 생성"),
        ("2", "데이터 점검", "validate-data", "validation.json", "빠진 값·주의점 확인"),
        ("3", "모델 학습", "train", "model.pt", "사례 요약법 배우기"),
        ("4", "좌표 만들기", "embed", "embeddings.npz", "사례 → 숫자 좌표"),
        ("5", "분석", "analyze", "review_cards.json 등", "비교·순위·카드"),
        ("6", "평가", "evaluate", "evaluation.json", "결과 믿을 만한가"),
    ]
    flow_html = '<div class="flow">' + '<div class="arrow">→</div>'.join(
        f'<div class="step"><div class="step-no">{n}</div><div class="step-name">{t}</div>'
        f'<div class="step-cmd">{c}</div><div class="step-desc">{d}</div><div class="step-file">{f}</div></div>'
        for n, t, c, f, d in flow) + "</div>"
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">2</span>전체 흐름 한눈에 보기</h2>
<p>여섯 단계를 순서대로 실행합니다. 각 단계는 앞 단계가 만든 파일을 읽어서 다음 파일을 만듭니다.</p>
{flow_html}
<h3>준비 (처음 한 번만)</h3>
<p>Python 3.11 이상과 <code>uv</code>가 필요합니다. GPU나 인터넷 연결 없이 일반 노트북 CPU로 충분합니다.</p>
{code("uv sync")}
{code("uv run pytest        # 모든 테스트가 통과하면 설치 완료")}
<h3>여섯 단계를 한 번에 실행하기</h3>
{code("uv run simcompare all --config configs/demo.yaml --demo-out demo_data")}
<p>결과는 <code>outputs/demo/</code>에 저장됩니다. 실행 중에
<code>DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다</code>라는 문구가 보이면 정상입니다.
이 문서를 만들 때는 전체가 <b>약 {total_sec:.0f}초</b> 걸렸습니다.</p>
<p>아래부터는 같은 과정을 <b>한 단계씩</b> 실행하며 무엇이 만들어지는지 봅니다.</p>
{table(["단계", "명령", "걸린 시간"], [[n, f"<code>simcompare {c}</code>", f"{secs[c]:.1f}초"] for n, _, c, _, _ in flow])}
<p class="small">설정 파일 <code>configs/demo.yaml</code>의 주요 값: 이물 크기 1·2·4·8·16 µm, 학습 20회 반복, 평가용 비율 25%, seed 0.
seed가 같으면 몇 번을 돌려도 같은 결과가 나옵니다.</p>
</section>""")

    # ---------------- 3. 더미 데이터
    demo = logs["demo-data"]
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">3</span>1단계 · 더미 데이터 만들기</h2>
{code("uv run simcompare demo-data --out demo_data --seed 0")}
<p>실제 자료가 없어도 전체 흐름을 시험할 수 있도록 <b>가짜 제품 6개</b>(P1~P6)를 만듭니다.
제품마다 불량 3종류가 있고, P1은 설계를 한 번 바꾼 v2가 더 있어 <b>사례는 모두 {demo['n_bundles']}개</b>입니다.</p>
<pre class="tree">demo_data/
├── bundles.csv       사례 목록 (제품·버전·불량 종류·공장·크기 등)
├── layouts.csv       전극 그림 파일 위치
├── hotspots.csv      hotspot 그림 파일 위치 (이물 크기별)
├── ca_records.csv    이물 크기별 CA 값
├── fab.json          공장별 이물 크기 분포
├── geometry.json     두께·전극 간격·선폭 같은 형상 수치
├── arrays/, images/  실제 그림 파일
└── summary.json      생성 요약</pre>
<h3>사례 하나는 이렇게 생겼습니다</h3>
{figure("inputs.svg", "그림 1. P1 v1의 ‘옆 전극 붙음’ 사례. 왼쪽 두 장은 전극 그림, 오른쪽 세 장은 이물 크기별 hotspot입니다. 이물이 클수록 위험한 자리가 넓고 진해집니다.", "86%")}
{figure("fab.svg", "그림 2. 공장별 이물 크기 분포. FAB_A는 작은 이물이, FAB_B는 중간~큰 이물이 많습니다. 회색은 순위 비교용으로 모든 크기를 똑같이 둔 기준 분포입니다.", "60%")}
</section>""")

    # ---------------- 4. 점검
    vd = logs["validate-data"]
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">4</span>2단계 · 데이터 점검</h2>
{code("uv run simcompare validate-data --config configs/demo.yaml")}
<p>학습 전에 자료 상태를 확인합니다. 자료를 고치지는 않고 <b>보고만</b> 합니다. 화면에는 요약이, 파일
<code>validation.json</code>에는 자세한 내용이 남습니다.</p>
{json_excerpt(vd, ["n_bundles", "n_warnings", "confounding"])}
<h3>1단계에서 일부러 넣어 둔 ‘빈틈’</h3>
<p>실제 자료처럼 값이 빠지거나 조건이 다른 경우를 섞어 두었습니다. 이 단계가 이를 제대로 알려 주는지 확인합니다.</p>
<ul>
  <li><b>P6</b>은 실제 크기(µm) 정보가 없습니다 → 면적 기준 비교에서 빠져야 합니다.</li>
  <li><b>P4</b>는 8 µm hotspot과 형상 수치가 없습니다.</li>
  <li><b>P3</b>는 CA 2 µm 값이, <b>P2</b>는 CA 16 µm 값이 없습니다.</li>
  <li><b>P1 v2</b>는 P1 v1에서 전극 간격을 넓힌 변경 설계입니다.</li>
  <li><b>P5</b>는 다른 제품보다 위험 수치를 3배 크게 만들었습니다.</li>
</ul>
{figure("readiness.svg", "그림 3. 제품별로 어떤 자료가 있는지 정리한 표 (validation.json과 입력 파일에서 계산). 주황 칸이 빠진 자료입니다 — 1단계에서 넣어 둔 빈틈이 모두 잡혔습니다.", "90%")}
<h3>이 결과를 어떻게 읽나요</h3>
{table(["확인 항목", "이번 결과", "의미"], [
    ["실제 크기 없음", ", ".join(val["physical_scale"]["missing_sample_ids"]), "이 사례는 면적당 CA 비교에서 빠집니다."],
    ["빠진 CA 값", f"2 µm {val['missing_inputs']['ca@2']}건, 16 µm {val['missing_inputs']['ca@16']}건", "빈 값을 0으로 채우지 않고 ‘없음’으로 둡니다."],
    ["hotspot ≈ 전극 그림", f"{len(flagged)}건 ({', '.join(short(s) for s in flagged)}의 끊김)", "hotspot이 전극 모양을 거의 그대로 따라가 같은 정보를 두 번 쓸 수 있다는 주의."],
    ["공장 × 제품군 겹침", val["confounding"]["status"], "제품군 F3가 두 공장에 모두 있어 공장 효과와 제품군 효과를 <b>어느 정도</b> 나눠 볼 수 있습니다."],
    *([["픽셀 위치 대응 확인 불가", ", ".join(sorted({w.split(":")[0] for w in pixel_warn})),
        "전극 그림과 hotspot의 픽셀 크기 정보가 없어 두 그림이 같은 위치를 가리키는지 확인할 수 없습니다."]] if pixel_warn else []),
    *([["CA 값 범위", f"최대 {val['ca_value_range']['observed_max']:.3f} (0.03 초과 {val['ca_value_range']['n_above_typical_0_03']}건)",
        "일반 범위보다 큰 값이 있어 원자료 확인이 필요하다는 안내입니다(오류는 아님)."]]
      if val.get("ca_value_range", {}).get("n_above_typical_0_03") else []),
    ["학습/평가 나누기", "가능" if val["split_feasible"] else "불가", f"서로 독립인 제품 묶음이 {val['independent_groups']}개 있습니다."],
])}
{note("경고가 있어도 다음 단계는 실행됩니다. 하지만 실제 자료라면 <b>경고부터 읽고</b> 자료를 보완할지 먼저 판단하세요.")}
</section>""")

    # ---------------- 5. 학습
    tr = logs["train"]
    first, last = hist.iloc[0], hist.iloc[-1]
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">5</span>3단계 · 모델 학습</h2>
{code("uv run simcompare train --config configs/demo.yaml")}
<p>전극 그림·hotspot·CA·Fab 분포·형상 수치를 한꺼번에 받아 <b>짧은 숫자 묶음으로 압축했다가 다시 원래대로 복원</b>하도록
모델(오토인코더)을 연습시킵니다. 복원을 잘하려면 사례의 핵심을 담아야 하므로, 압축된 숫자가 곧 그 사례의 <b>요약</b>이 됩니다.</p>

<h3>연습용과 시험용을 제품 단위로 나눕니다</h3>
<div class="split">
  <div class="side train"><div class="side-title">학습용 · {tr['n_train']}개 사례</div>
    {''.join(f'<span class="chip">{x}</span>' for x in train_products)}</div>
  <div class="side test"><div class="side-title">평가용 · {tr['n_test']}개 사례</div>
    {''.join(f'<span class="chip hollow">{x}</span>' for x in test_products)}</div>
</div>
<p>같은 제품이나 같은 설계 계열(예: P1 v1과 v2)은 <b>반드시 한쪽에만</b> 들어갑니다. 연습문제와 시험문제가 겹치면 실력을
부풀려 보게 되기 때문입니다. 나눈 결과는 <code>split.json</code>에 있습니다.</p>

{figure("loss.svg", "그림 4. 학습 반복에 따른 입력별 복원 오차 (train_history.csv). 낮을수록 잘 복원한다는 뜻입니다. 칸마다 세로축 범위가 다릅니다.")}
<h3>이 결과를 어떻게 읽나요</h3>
<ul>
  <li><b>hotspot</b>({first['hotspot']:.2f} → {last['hotspot']:.2f})과 <b>전극 그림</b>({first['layout']:.2f} → {last['layout']:.2f})은 꾸준히 줄었습니다.</li>
  <li><b>CA·Fab·형상 수치</b>는 오르내림이 큽니다. 학습 사례가 {tr['n_train']}개뿐이라 자연스러운 모습입니다.</li>
  <li>오차가 작다고 해서 비교·검색 결과가 좋다는 보장은 <b>없습니다</b>. 그 판단은 5·6단계에서 합니다.</li>
</ul>
<p class="small">함께 만들어지는 파일: <code>model.pt</code>(학습된 모델), <code>train_history.csv</code>(그림 4의 원자료),
<code>split.json</code>(나눈 결과), <code>run_record.json</code>(seed·설정·버전 기록).</p>
</section>""")

    # ---------------- 6. 좌표
    em = logs["embed"]["spaces"]
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">6</span>4단계 · 사례를 좌표로 바꾸기</h2>
{code("uv run simcompare embed --config configs/demo.yaml")}
<p>모든 사례를 숫자 좌표로 바꿉니다. 좌표를 만드는 방법이 다섯 가지이고, 각각을 ‘공간’이라고 부릅니다.
가장 중요한 구분은 <b>위험 수치(CA·hotspot)를 썼느냐</b>입니다.</p>
{table(["공간 이름", "무엇으로 만들었나", "위험 수치 사용", "크기"], [
    ["<code>shape_only</code>", "전극 모양·실제 크기·두께 같은 <b>모양 정보만</b> (단순 통계 방법)", "아니오", f"{em['shape_only'][1]}개 숫자"],
    ["<code>with_risk_baseline</code>", "모양 + hotspot·CA·Fab 요약값 (단순 통계 방법)", "예", f"{em['with_risk_baseline'][1]}개 숫자"],
    ["<code>with_risk_ae</code>", "3단계에서 학습한 모델의 요약", "예", f"{em['with_risk_ae'][1]}개 숫자"],
    ["<code>ae_struct</code>", "모델 요약 중 설계·시뮬레이션 쪽 부분", "예", f"{em['ae_struct'][1]}개 숫자"],
    ["<code>ae_fab</code>", "모델 요약 중 공장 분포 쪽 부분", "예", f"{em['ae_fab'][1]}개 숫자"],
])}
{figure("map.svg", "그림 5. 끊김 사례 7개를 두 공간에서 평면 지도로 그린 것 (embeddings.npz를 2차원으로 줄여 표시). 점 사이 거리가 가까울수록 비슷한 사례입니다.")}
<h3>이 결과를 어떻게 읽나요</h3>
<ul>
  <li>① 모양만 보면 <b>P5</b>는 다른 제품들 사이에 섞여 있습니다.</li>
  <li>② 위험 수치를 넣으면 <b>P5가 홀로 멀리</b> 떨어집니다. P5의 CA가 다른 제품보다 약 {p5_ratio:.0f}배 크기 때문입니다.</li>
  <li><b>P1 v1과 v2</b>는 두 지도 모두에서 가깝습니다. 설계를 조금 바꾼 같은 계열이기 때문입니다.</li>
</ul>
{note("<b>주의</b> — 위험 수치를 넣어 만든 공간에서 “위험이 비슷한 사례”를 찾은 것은 새로운 발견이 아닙니다. 입력한 값을 다시 본 것에 가깝습니다. 결과 파일에서는 <code>reuses_risk_inputs=True</code>로 표시됩니다.")}
</section>""")

    # ---------------- 7. 분석
    an = logs["analyze"]
    q = "P5-v1-OPEN"
    space_names = {"shape_only": "모양만", "with_risk_baseline": "모양+위험 (통계)", "with_risk_ae": "모양+위험 (모델)",
                   "ae_struct": "모델 · 설계 쪽", "ae_fab": "모델 · 공장 쪽"}
    nb_rows = []
    for space, g in nb[nb["query_id"] == q].groupby("space", sort=False):
        g = g.sort_values("rank")
        nb_rows.append([f"<code>{space}</code><br><span class='small'>{space_names.get(space, '')}</span>",
                        " → ".join(f"{short(n)}" for n in g["neighbor_id"]),
                        "예 (독립 발견 아님)" if g["reuses_risk_inputs"].iloc[0] else "<b>아니오</b>"])
    lo_low = loo.loc[loo["neighbor_jaccard"].idxmin()]
    top_low = loo.loc[loo["top_items_jaccard"].idxmin()]
    p1_drop = []
    for code_ in ("OPEN", "LSHORT", "VSHORT"):
        v1, v2 = rank.set_index("sample_id").loc[[f"P1-v1-{code_}", f"P1-v2-{code_}"], "ca_max"]
        p1_drop.append(100 * (v1 - v2) / v1)
    shape_nb = nb[(nb["query_id"] == q) & (nb["space"] == "shape_only")].sort_values("rank")["neighbor_id"]
    p3, p4, p1 = opens.loc["P3-v1-OPEN"], opens.loc["P4-v1-OPEN"], opens.loc["P1-v1-OPEN"]
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">7</span>5단계 · 분석</h2>
{code("uv run simcompare analyze --config configs/demo.yaml")}
<p>분석 단계는 네 가지 질문에 답하는 파일을 만듭니다. 이번 실행에서 검토 카드 {an['n_cards']}장,
비슷한 사례 {an['n_neighbor_rows']}줄이 만들어졌습니다.</p>
{table(["질문", "보는 파일"], [
    ["제품별로 위험이 얼마나 다른가?", "<code>risk_table.csv</code>"],
    ["크기·공장을 반영하면 순위가 바뀌나?", "<code>ranking.csv</code>, <code>rank_agreement.json</code>"],
    ["이 사례와 비슷한 다른 제품 사례는?", "<code>neighbors.csv</code>, <code>clusters.json</code>"],
    ["결과가 제품 하나에 좌우되지 않나?", "<code>loo_stability.csv</code>"],
    ["유사 제품 비교군 안에서 어느 전극·layer가 위험하고 무엇이 다른가?", "<code>neighborhood_comparison.csv</code>, <code>neighborhood_geometry.csv</code>"],
    ["회피안 후보와 변경본 재비교는?", "<code>edit_candidates.json</code>, <code>variant_comparison.csv</code>"],
    ["한 장으로 정리하면?", "<code>review_cards.json</code>"],
    ["그림으로 한눈에 보려면?", ", ".join(f"<code>figures/{esc(n)}</code>" for n in auto_figs)],
])}

<h3>7-1. 제품별 위험 비교</h3>
{figure("ca.svg", "그림 6. ① 끊김 불량의 이물 크기별 CA (risk_table.csv, 양쪽 축 모두 로그 눈금). ② P1 설계 변경 전후 가장 큰 CA의 변화율.")}
<ul>
  <li>① <b>P5</b>는 모든 이물 크기에서 CA가 가장 큽니다. <b>P3</b>는 2 µm 값이, <b>P2</b>는 16 µm 값이 빠져 선이 끊겨 있습니다 — 없는 값을 지어내지 않습니다.</li>
  <li>② 전극 간격을 넓힌 <b>P1 v2</b>는 세 불량 모두 가장 큰 CA가 {min(p1_drop):.0f}~{max(p1_drop):.0f}% 줄었습니다. 다만 이것은 가짜 데이터의 수식이 그렇게 만든 결과입니다.</li>
</ul>
</section>

<section class="pagebreak">
<h3>7-2. 크기·공장을 반영하면 순위가 바뀝니다</h3>
{figure("rank.svg", "그림 7. 끊김 불량 7개 사례의 위험 순위 변화 (ranking.csv). 1위가 가장 위험합니다. 크게 바뀐 사례만 색으로 표시했습니다.")}
<ul>
  <li>① <b>P3</b>는 CA 원값으로는 {int(p3['rank_ca_max'])}위지만 면적당으로 보면 {int(p3['rank_ca_area_max'])}위입니다.
      화소가 커서(80×120 µm) CA가 크게 나온 것입니다. 반대로 작은 제품 <b>P4</b>는 {int(p4['rank_ca_max'])}위 → {int(p4['rank_ca_area_max'])}위로 올라갑니다.</li>
  <li>② <b>P4</b>는 실제 공장(FAB_B) 분포를 쓰면 {int(p4['rank_caa_reference'])}위 → {int(p4['rank_caa_actual'])}위로 올라갑니다.
      FAB_B에 큰 이물이 많기 때문입니다. 작은 이물이 많은 FAB_A의 <b>P1 v1</b>은 {int(p1['rank_caa_reference'])}위 → {int(p1['rank_caa_actual'])}위로 내려갑니다.</li>
  <li>두 순위가 얼마나 비슷한지는 <code>rank_agreement.json</code>에 0~1 사이 값(1이면 완전히 같음)으로 나옵니다:
      끊김 기준 공장 반영 전후 {agree_by['lateral_open']['spearman_actual_vs_reference']:.2f}, 크기 보정 전후 {agree_by['lateral_open']['spearman_raw_vs_area']:.2f}.</li>
</ul>
{note("CAA는 이번 실행에서 <b>DEMO 규칙</b>으로 계산됐습니다. 실제 자료에서는 계산 규칙이 확정되기 전까지 CAA 칸이 이유와 함께 비어 있습니다.")}

<h3>7-3. 비슷한 사례 찾기</h3>
<p><code>neighbors.csv</code>에는 사례마다 공간별로 가장 가까운 다른 제품 사례 3개가 있습니다. 같은 불량 종류·같은 전극 역할끼리만 비교합니다.
아래는 가장 위험한 <b>P5 끊김</b> 사례의 결과입니다.</p>
{table(["공간", "가까운 순서", "위험 수치 사용"], nb_rows)}
<p>공간마다 답이 조금씩 다릅니다. <b>모양만 쓴 공간</b>({" → ".join(short(x) for x in shape_nb)})이 “위험과 상관없이 모양이 비슷한 사례”라는
독립적인 근거이고, 나머지는 참고용입니다.</p>
</section>

<section class="pagebreak">
<h3>7-4. 제품 하나를 빼도 결과가 유지되나요</h3>
{figure("loo.svg", "그림 8. 제품을 하나씩 빼고 다시 분석했을 때 목록이 얼마나 그대로인지 (loo_stability.csv). 1이면 완전히 같고 0이면 전혀 겹치지 않습니다.", "68%")}
<ul>
  <li><b>{lo_low['held_out_product']}</b>를 빼면 비슷한 사례 목록이 {lo_low['neighbor_jaccard']:.2f}만 유지됩니다. 여러 사례의 ‘이웃’ 역할을 {lo_low['held_out_product']}가 많이 하고 있다는 뜻입니다.</li>
  <li><b>{top_low['held_out_product']}</b>처럼 위험 상위 제품을 빼면 상위 목록이 {top_low['top_items_jaccard']:.2f}로 떨어집니다. 상위 목록이 바뀌는 건 당연하므로 크게 걱정할 일은 아닙니다.</li>
  <li>값이 낮은 제품이 있으면 “그 제품 하나 때문에 나온 결론인지” 확인해 보세요.</li>
</ul>
{card_html(cards["cards"][0], opens)}
</section>

<section class="pagebreak">
<h3>7-6. 자동으로 저장되는 그림 (figures/)</h3>
<p><code>analyze</code>를 실행하면 위에서 본 표 가운데 세 가지가 그림으로도 저장됩니다. 파일은
<code>outputs/demo/figures/</code>에 있고, <b>브라우저로 열면</b> 한글 설명까지 그대로 보입니다.
추가 설치는 필요 없습니다.</p>
{table(["파일", "보여 주는 것", "이 문서의 비슷한 그림"], [
    ["<code>rank_shift.svg</code>", "크기 보정·Fab 반영 전후 순위 변화. 가장 크게 바뀐 두 사례만 색으로 표시", "그림 7"],
    ["<code>ca_curves.svg</code>", "이물 크기별 CA 곡선. CA가 가장 큰 사례를 주황으로 표시하고 빠진 값은 선을 끊음", "그림 6 ①"],
    ["<code>loo_stability.svg</code>", "제품을 하나씩 뺐을 때 두 목록의 유지율 막대", "그림 8"],
])}
<p>이 문서의 그림은 설명을 위해 일부 사례만 골라 그렸지만, 자동 그림은 <b>모든 비교 그룹</b>(불량 종류·전극 역할·layer 조합)을
한 줄씩 세로로 쌓아 보여 줍니다. 아래는 이번 실행에서 실제로 저장된 <code>rank_shift.svg</code>의 첫 번째 그룹입니다.</p>
{figure("auto_rank_shift.svg", "그림 10. analyze가 자동으로 저장한 figures/rank_shift.svg의 윗부분 (끊김 불량 그룹). 이름 뒤 *는 한쪽 값이 없어 순위를 비교하지 못한 사례입니다.")}
{note("그림은 결과 표를 옮긴 것일 뿐 새로운 판단을 더하지 않습니다. 사례 ID가 길면 그림에서는 줄여 표시하고, 전체 ID는 CSV 파일에 있습니다.")}
</section>""")
    p.append(hood_section(hood, hood_geo, edits, variants))

    # ---------------- 8. 평가
    lo = ev["leave_one_group_out"]
    names_ = {"layout": "전극 그림", "hotspot": "hotspot", "ca": "CA", "fab": "Fab", "geometry": "형상 수치"}
    better, worse = [], []
    for key, cfg in [("layout", "image_only"), ("hotspot", "image_only"), ("ca", "numeric_only"), ("fab", "numeric_only"), ("geometry", "numeric_only")]:
        (better if ab["full"]["test_losses"][key] < ab[cfg]["test_losses"][key] else worse).append(names_[key])
    held = ev.get("held_out_product_evidence") or {}
    held_html = ""
    if held.get("queries"):
        mark = {True: "같음", False: "다름", None: "판단 불가"}
        qrows = [[f"<code>{esc(x['query_id'])}</code>", " · ".join(short(n) for n in x.get("neighbors", [])) or "-",
                  mark[x.get("trend_agreement")]] for x in held["queries"]]
        rate = held.get("trend_agreement_rate")
        held_html = (
            "<h3>8-2. 처음 보는 제품에서도 경향이 반복되나요</h3>"
            "<p>평가용 사례마다 <b>학습용 제품 중 모양이 가장 비슷한 사례</b>를 찾습니다. 그다음 평가 사례의 면적당 CA와 "
            "이웃들의 평균이 <b>둘 다 학습 사례의 중간값보다 높거나, 둘 다 낮으면</b> ‘같음’으로 셉니다. "
            "정답표가 없으므로 정확도가 아니라 “모양이 비슷하면 위험 경향도 비슷한가”를 보는 참고 근거입니다.</p>"
            + table(["평가 사례", "모양이 비슷한 학습 사례", "위험 경향"], qrows)
            + (f"<p>경향이 같았던 비율: <b>{rate:.2f}</b> ({held['n_scored']}건 중). "
               f"평가 제품이 {len(test_products)}개뿐이라 이 값 하나로 판단하지 않습니다.</p>" if rate is not None else ""))
    j = {k: ab[k]["mean_neighbor_jaccard_vs_shape_only_baseline"] for k in ("image_only", "numeric_only", "full")}
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">8</span>6단계 · 평가</h2>
{code("uv run simcompare evaluate --config configs/demo.yaml")}
<p>학습에 쓰지 않은 평가용 제품({', '.join(test_products)})으로 결과를 점검합니다. 결과는 <code>evaluation.json</code>에 있고,
학습·평가가 섞이지 않았는지 검사(<code>leakage_check</code>)는 <b>{ev['split']['leakage_check']}</b>였습니다.</p>

<h3>8-1. 입력을 섞으면 도움이 되나요</h3>
<p>같은 조건으로 ‘그림만’, ‘숫자만’, ‘모두’ 넣은 모델을 각각 학습해 평가용 제품을 얼마나 잘 복원하는지 비교합니다.</p>
{figure("ablation.svg", "그림 9. 입력 종류별 평가 복원 오차 (evaluation.json의 ablation). 회색은 같은 종류 입력만 넣은 모델, 파랑은 전부 넣은 모델입니다.", "72%")}
<ul>
  <li>모두 넣었을 때 오차가 줄어든 입력: <b>{"·".join(better) or "없음"}</b> / 오히려 늘어난 입력: <b>{"·".join(worse) or "없음"}</b>.</li>
  <li>평가 제품이 {len(test_products)}개뿐이라 이 차이로 “섞는 게 낫다/못하다”를 판단할 수 <b>없습니다</b>. 실제 자료에서 다시 확인해야 합니다.</li>
  <li>각 모델이 찾은 비슷한 사례가 ‘모양만 쓴 공간’과 얼마나 겹치는지(0~1): 그림만 {j['image_only']:.2f}, 숫자만 {j['numeric_only']:.2f}, 모두 {j['full']:.2f}.</li>
</ul>

{held_html}
<h3>8-3. 공장·제품군을 통째로 빼 보기</h3>
{table(["빼 본 기준", "판정", "설명"], [
    ["공장(Fab)", esc(lo["fab_id"]["verdict"]), "공장 하나를 통째로 빼고 나머지로 설명이 되는지 봅니다."],
    ["제품군", esc(lo["product_family"]["verdict"]), "제품군 하나를 통째로 빼고 봅니다."],
])}
<p>판정은 세 가지 중 하나입니다. <b>주의해서 평가 가능</b>: 비교할 수 있음. <b>평가 불가</b>: 뺄 그룹이 부족함.
<b>효과 분리 불가</b>: 공장과 제품군이 늘 같이 움직여 둘 중 무엇의 영향인지 구분할 수 없음.</p>
{note("복원 오차가 작다는 것은 “요약을 잘했다”는 뜻일 뿐, “비교·해석이 옳다”는 뜻이 아닙니다.")}
</section>""")

    # ---------------- 9. 읽는 순서
    p.append(f"""
<section class="pagebreak">
<h2><span class="no">9</span>결과를 읽는 순서와 주의할 점</h2>
<p>결과 파일이 {n_files}개나 되지만, 아래 순서로 보면 충분합니다.</p>
<ol class="checklist">
  <li><b><code>validation.json</code>의 경고</b> — 빠진 자료, 실제 크기 없는 제품, 공장·제품군 겹침부터 확인합니다. (그림 3)</li>
  <li><b><code>split.json</code></b> — 어떤 제품이 평가용으로 갔는지 확인합니다. (5장)</li>
  <li><b><code>review_cards.json</code></b> — 불량 종류별 카드에서 대표·반대 사례, 검토 후보, ‘자료 없음’, ‘전문가 확인 필요’를 읽습니다. (7-4)</li>
  <li><b><code>ranking.csv</code></b> — 크기·공장 반영 전후로 순위가 크게 바뀐 제품을 찾습니다. <code>figures/rank_shift.svg</code>로 먼저 훑어보면 빠릅니다. (그림 7·10)</li>
  <li><b><code>neighbors.csv</code></b> — 모양만 쓴 공간(<code>reuses_risk_inputs=False</code>)의 이웃을 우선 봅니다. (7-3)</li>
  <li><b><code>loo_stability.csv</code>, <code>evaluation.json</code></b> — 결론이 제품 하나에 좌우되지 않는지 확인합니다. (그림 8·9)</li>
</ol>
<h3>헷갈리기 쉬운 세 가지</h3>
<div class="three">
  <div class="box"><div class="box-title">비슷하다 ≠ 원인이다</div>
    이 도구는 “같이 나타난다”까지만 보여 줍니다. 원인 판단은 설계 전문가의 몫입니다.</div>
  <div class="box"><div class="box-title">CAA ≠ 수율</div>
    CAA는 제품끼리 비교하기 위한 지표입니다. 실제 불량률·수율로 읽으면 안 됩니다.</div>
  <div class="box"><div class="box-title">군집 번호에는 뜻이 없음</div>
    <code>clusters.json</code>의 0번·1번은 이름표일 뿐, 0번이 더 안전하다는 뜻이 아닙니다.</div>
</div>

<h2 style="margin-top:28px"><span class="no">10</span>내 데이터로 바꿔 실행하기</h2>
<ol>
  <li><b>자료 정리</b>: 1단계의 <code>demo_data/</code>와 같은 모양으로 <code>bundles.csv</code>, <code>fab.json</code> 등을 준비합니다.
      자세한 열 이름은 <code>docs/data_contract.md</code>에 있습니다.</li>
  <li><b>설정 복사</b>: <code>configs/real.example.yaml</code>을 <code>configs/real.yaml</code>로 복사하고 자료 위치와 이물 크기 목록을 바꿉니다.</li>
  <li><b>점검 먼저</b>: <code>uv run simcompare validate-data --config configs/real.yaml</code> 후 경고를 읽습니다.</li>
  <li><b>나머지 실행</b>: train → embed → analyze → evaluate 순서로 같은 설정 파일을 넣어 실행합니다.</li>
</ol>
{note("실제 모드(<code>mode: real</code>)에서는 확정되지 않은 계산을 하지 않습니다. 예를 들어 CAA 규칙이 정해지기 전에는 CAA 칸이 비어 있고, 위아래 전극 붙음 사례에 적용 공정 layer가 없으면 오류로 멈춥니다. 확정이 필요한 항목은 <code>docs/open_questions.md</code>에 있습니다.")}
<h3>자주 만나는 메시지</h3>
{table(["화면에 나온 문구", "해결"], [
    ["<code>…model.pt이(가) 없습니다. 먼저 'simcompare train'을 실행하세요</code>", "train을 먼저 실행합니다."],
    ["<code>체크포인트의 샘플이 현재 데이터에 없습니다(재학습 필요)</code>", "자료가 바뀌었으니 train부터 다시 실행합니다."],
    ["<code>알 수 없는 설정 키 …</code>", "설정 파일 오타입니다. <code>configs/real.example.yaml</code>과 비교합니다."],
    ["<code>독립 그룹(제품·설계 계열)이 2개 미만입니다</code>", "비교할 제품이 너무 적습니다. 제품을 늘립니다."],
])}
<p class="small">더 자세한 설명: <code>docs/usage.md</code>(실행 가이드), <code>docs/model_design.md</code>(모델 구조와 한계).
이 문서는 <code>uv run --with matplotlib python docs/walkthrough/build.py</code>로 다시 만들 수 있습니다.</p>
</section>""")

    return HTML_HEAD + "\n".join(p) + "</body></html>"


FEATURE_NAMES = {"pair:min_gap": "최소 간격", "pair:near_gap_len": "좁은 간격 길이",
                 "pair:overlap_area": "겹침 면적", "pair:a_min_width": "전극 a 최소 선폭",
                 "pair:b_min_width": "전극 b 최소 선폭"}


def hood_section(hood: pd.DataFrame, geo: pd.DataFrame, edits: pd.DataFrame, variants: pd.DataFrame) -> str:
    """7-7절: 유사 제품 비교군, 참고 사례와의 치수 차이, 회피안 후보, 변경본 재비교."""
    query = "P5-v1-LSHORT"
    row = hood.set_index("query_id").loc[query]
    hood_rows = [[short(query) + " (질의)", "-"]] + [
        [short(n), f"{i + 1}번째로 가까움"] for i, n in enumerate(str(row["neighbor_ids"]).split(";"))]
    geo_rows = [[FEATURE_NAMES.get(g["feature"], g["feature"]), f"{g['query_value']:g}", f"{g['reference_value']:g}",
                 f"{g['delta']:+g}", "늘리기" if g["review_direction"] == "increase" else "줄이기"]
                for _, g in geo[geo["query_id"] == query].iterrows()]
    mine = edits[(edits["query_id"] == query) & (edits["status"] == "proposed")]
    op_names = {"trim_near": "국소 후퇴", "remove_overlap": "겹침 국소 제거"}
    edit_rows = [[f"<code>{esc(e['candidate_id'])}</code>", op_names.get(e["op"], e["op"]),
                  f"{esc(e['electrode_role'])} ({esc(e['layer'])})", f"{e['amount_um']:g} µm",
                  esc(str(e["roi_um"]).replace(";", ", ")), esc(e["roi_source"])] for _, e in mine.iterrows()]
    skipped = edits[edits["status"] == "skipped"]
    fl = {"lateral_open": "끊김", "lateral_short": "옆 전극 붙음", "vertical_short": "위아래 전극 붙음"}
    var_rows = [[fl.get(v["failure_type"], v["failure_type"]), f"{v['parent_ca_area']:.3g}", f"{v['variant_ca_area']:.3g}",
                 f"{(v['ratio'] - 1) * 100:+.0f}%", esc(v["compared_sizes"].replace(";", ", ")) + " µm"]
                for _, v in variants[variants["status"] == "ok"].iterrows()]
    return f"""
<section class="pagebreak">
<h3>7-7. 유사 제품 비교군과 회피안 후보</h3>
<p>질의 사례와 <b>오토인코더 공간</b>에서 가까운 다른 제품 사례를 묶어 비교군을 만들고, 그 안에서만 면적당 CA를 비교합니다.
사례마다 관측한 이물 크기가 다를 수 있어 비교군이 <b>모두 관측한 크기</b>만 씁니다. 아래는 <b>P5 옆 전극 붙음</b> 사례입니다
(<code>neighborhood_comparison.csv</code>).</p>
{table(["비교군", "거리"], hood_rows)}
<p>비교 크기 {esc(str(row["compared_sizes"]).replace(";", ", "))} µm에서 P5는 비교군 <b>{int(row["query_rank"])}위</b>이고,
이웃 중앙값의 <b>{row["risk_ratio_to_median"]:.1f}배</b>입니다. 비교군에서 가장 위험이 낮은 <b>{short(row["reference_id"])}</b>를
참고 사례로 삼아 전극 쌍 치수를 비교합니다(<code>neighborhood_geometry.csv</code>, 단위 µm).</p>
{table(["치수", "P5", short(row["reference_id"]), "차이", "참고 사례 쪽 방향"], geo_rows)}
<p>이 차이로 <b>국소 편집 후보</b>를 만듭니다(<code>edit_candidates.json</code>). 전극 전체를 바꾸지 않고 hotspot 부근 영역(ROI, µm)
안에서만 후퇴·제거하며, 양은 간격 차이의 0.5배와 1배, 대상은 두 전극 각각입니다.</p>
{table(["후보", "편집", "대상 전극", "양", "ROI (x0, y0, x1, y1)", "ROI 근거"], edit_rows)}
{note(f"끊김(open)의 선폭 보강처럼 금속을 추가하는 편집은 아직 지원하지 않아 {len(skipped)}건이 사유와 함께 <code>edit_candidates.csv</code>에 남습니다. "
      "후보는 관찰된 차이에 근거한 검토 대상일 뿐이며 채택은 설계자가 판단합니다.")}
<h3>변경본을 다시 계산해 원안과 비교합니다</h3>
<p>후보 JSON은 particle-sim <code>make-variant</code>가 그대로 읽어 편집한 마스크로 변경본 입력을 만듭니다. 변경본을
<code>simulate-nominal</code> → <code>export-simcompare</code>로 계산해 <code>merge-exports</code>로 원안과 합치면,
<code>variant_comparison.csv</code>가 <b>불량 종류별</b>로 원안 대비 변화를 보여 줍니다. 다른 불량이 늘었는지도 이 표에서 봅니다.
아래는 더미 데이터에 들어 있는 변경본 <b>P1 v2</b>(전극 간격 확대)의 결과입니다.</p>
{table(["불량", "원안 P1 v1", "변경본 P1 v2", "변화", "비교 크기"], var_rows)}
{note("다른 불량의 증가를 어디까지 허용할지와 설계 규칙은 도구가 판정하지 않습니다. 실제 재시뮬레이션에는 원안의 particle-sim 입력(manifest와 원본 마스크)이 필요합니다.", "warn")}
</section>"""


def crop_svg(src: Path, dst: Path, height: int) -> None:
    """자동 생성 SVG의 윗부분(height px)만 보이도록 크기와 viewBox를 바꿔 복사한다."""
    import re

    text = src.read_text(encoding="utf-8")
    text = re.sub(r'height="[0-9.]+" viewBox="0 0 ([0-9.]+) [0-9.]+"',
                  lambda m: f'height="{height}" viewBox="0 0 {m.group(1)} {height}"', text, count=1)
    dst.write_text(text, encoding="utf-8")


def card_html(card: dict, opens: pd.DataFrame) -> str:
    """검토 카드 한 장을 읽기 쉬운 모양으로 그린다."""
    feat = {"geom:thk:M2": "M2 두께", "layout0_fill": "전극이 차지하는 비율", "geom:shape:gap": "전극 간격",
            "geom:shape:line_width": "선폭", "layout0_edge": "전극 경계 밀도",
            "layout0_aspect": "전극 가로세로 비율", "layout1_fill": "두 번째 전극이 차지하는 비율", "layout1_edge": "두 번째 전극 경계 밀도"}

    def ids(xs):
        return ", ".join(f"<b>{short(x)}</b>" for x in xs)

    rows = "".join(f"<li>{esc(x)}</li>" for x in card["expert_review_required"])
    unavailable = "<br>".join(esc(x) for x in card.get("unavailable_conditions", [])) or "없음"
    direction = {"increase": "늘리는 쪽", "decrease": "줄이는 쪽"}
    cands = card.get("improvement_candidates") or []
    cand_rows = "".join(
        f"<tr><td>{esc(feat.get(c['feature'], c['feature']))}</td><td>{c['high_risk_median']:.3g}</td>"
        f"<td>{c['lower_risk_median']:.3g}</td><td>{direction.get(c['review_direction'], c['review_direction'])}</td></tr>"
        for c in cands)
    cand_html = ("<div class='k' style='margin-top:8px'>설계 변경 검토 후보 (improvement_candidates)</div>"
                 "<table class='mini'><thead><tr><th>형상</th><th>위험 높은 사례(중앙값)</th><th>위험 낮은 사례(중앙값)</th>"
                 f"<th>검토해 볼 방향</th></tr></thead><tbody>{cand_rows}</tbody></table>") if cands else ""
    status = card.get("verification_status") or {}
    pending = sum(v == "pending" for v in status.values())
    external = sum(v == "external_review_required" for v in status.values())
    verify = f"이물 재시뮬레이션 대기 {pending}건 · 외부 전기/설계 검토 필요 {external}건" if status else "기록 없음"
    return f"""
<h3>7-5. 검토 카드 (review_cards.json)</h3>
<p>불량 종류·전극 역할·layer 조합마다 한 장씩, 앞의 결과를 모아 정리합니다. 아래는 끊김 불량 카드를 그대로 옮긴 것입니다.</p>
<div class="card">
  <div class="card-head"><span class="card-id">{esc(card['card_id'])}</span> 끊김 불량 · 전극 역할 data · layer {esc(card['layers'])}
    <span class="badge">비교 가능 · 제품 {card['n_comparable_products']}개</span></div>
  <div class="card-grid">
    <div><div class="k">대표 사례</div><div class="v">{ids(card['representative_cases'])}</div>
         <div class="hint">이 그룹에서 위험이 높은 쪽을 대표</div></div>
    <div><div class="k">반대 사례</div><div class="v">{ids(card['counter_cases'])}</div>
         <div class="hint">같은 그룹이지만 위험이 낮은 쪽</div></div>
    <div><div class="k">위험 높은 사례들의 공통 형상</div><div class="v">{', '.join(feat.get(f, f) for f in card['repeated_shape_features'])}</div>
         <div class="hint">값이 서로 비슷했던 특징 (원인이라는 뜻 아님)</div></div>
    <div><div class="k">비교할 수 없는 조건</div><div class="v">{unavailable}</div></div>
  </div>
  {cand_html}
  <div class="k" style="margin-top:8px">전문가 확인 필요</div><ul class="card-list">{rows}</ul>
  <div class="k" style="margin-top:6px">검증 상태</div><div class="v">{verify}</div>
  <div class="hint" style="margin-top:6px">검토 후보는 “위험이 높은 사례와 낮은 사례에서 값이 달랐던 형상”일 뿐입니다. 바꾸면 좋아진다는 뜻이 아니며,
  이물 재시뮬레이션과 전기 특성(저항·정전용량 등) 검토를 거쳐야 합니다.</div>
</div>"""


HTML_HEAD = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>simcompare 따라하기</title>
<style>
@page { size: A4; margin: 16mm 16mm 18mm 16mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-family: 'Apple SD Gothic Neo', sans-serif; font-size: 8pt; color: #8a8983; } }
@page :first { @bottom-center { content: none; } }
:root { --ink:#0b0b0b; --ink2:#52514e; --muted:#8a8983; --line:#e8e7e2; --soft:#f6f5f1;
        --blue:#2a78d6; --blue-soft:#eaf2fc; --orange:#eb6834; --orange-soft:#fdf0ea; --green-soft:#e8f6ef; }
html { background:#fff; }
body { font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif; color:var(--ink); font-size:10pt; line-height:1.62;
       margin:0; word-break:keep-all; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
h1 { font-size:28pt; line-height:1.25; margin:6px 0 14px; letter-spacing:-0.5px; }
h2 { font-size:16pt; margin:0 0 10px; padding-bottom:6px; border-bottom:2px solid var(--ink); display:flex; align-items:center; gap:10px; }
h2 .no { display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px; border-radius:6px;
         background:var(--ink); color:#fff; font-size:12pt; }
h3 { font-size:11.5pt; margin:18px 0 6px; break-after:avoid; }
p { margin:6px 0; }
ul, ol { margin:6px 0; padding-left:20px; } li { margin:3px 0; }
code { font-family:'SF Mono',Menlo,monospace; font-size:8.6pt; background:var(--soft); padding:1px 4px; border-radius:3px; }
.small { font-size:8.8pt; color:var(--ink2); }
.pagebreak { break-before:page; }
section { break-inside:auto; }
pre { font-family:'SF Mono',Menlo,monospace; font-size:8.6pt; border-radius:6px; padding:8px 12px; margin:8px 0; white-space:pre-wrap; break-inside:avoid; }
pre.cmd { background:#1f1f1d; color:#f4f3ee; }
pre.cmd .prompt { color:#8a8983; margin-right:6px; }
pre.out { background:var(--soft); color:var(--ink2); border:1px solid var(--line); }
pre.tree { background:var(--soft); color:var(--ink); border:1px solid var(--line); line-height:1.5; }
figure { margin:12px 0; break-inside:avoid; text-align:center; }
figure img { display:block; margin:0 auto; }
figcaption { font-size:8.6pt; color:var(--ink2); margin-top:6px; text-align:left; }
table { width:100%; border-collapse:collapse; margin:8px 0; font-size:9pt; break-inside:avoid; }
th { text-align:left; font-weight:700; color:var(--ink2); border-bottom:1.5px solid var(--ink); padding:5px 8px; }
td { border-bottom:1px solid var(--line); padding:5px 8px; vertical-align:top; }
table.terms td:first-child { white-space:nowrap; font-weight:700; width:24%; }
.note { border-left:3px solid var(--blue); background:var(--blue-soft); padding:8px 12px; border-radius:0 6px 6px 0; margin:10px 0; font-size:9.2pt; break-inside:avoid; }
.note.warn { border-left-color:var(--orange); background:var(--orange-soft); }
.tiles { display:flex; gap:10px; margin:12px 0; }
.tile { flex:1; border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.tile .num { font-size:18pt; font-weight:800; line-height:1.2; }
.tile .lbl { font-size:8.5pt; color:var(--ink2); }
.cover { padding-top:40px; }
.cover .kicker { font-size:10pt; font-weight:700; color:var(--blue); letter-spacing:1px; }
.cover .lead { font-size:11.5pt; color:var(--ink2); max-width:92%; }
.toc { margin-top:26px; border-top:1px solid var(--line); padding-top:12px; }
.toc-title { font-weight:700; margin-bottom:4px; }
.toc ol { columns:2; column-gap:30px; font-size:10pt; }
.two { display:flex; gap:12px; margin:12px 0; } .two > .box { flex:1; }
.three { display:flex; gap:10px; margin:10px 0; } .three > .box { flex:1; font-size:9pt; }
.box { border:1px solid var(--line); border-radius:8px; padding:10px 12px; break-inside:avoid; }
.box-title { font-weight:800; margin-bottom:4px; }
.box.good { background:var(--green-soft); border-color:#cfe9dc; }
.box.bad { background:var(--orange-soft); border-color:#f6d6c7; }
.flow { display:flex; align-items:stretch; gap:4px; margin:14px 0; }
.flow .arrow { align-self:center; color:var(--muted); font-size:12pt; }
.flow .step { flex:1; border:1px solid var(--line); border-radius:8px; padding:8px 6px; text-align:center; background:#fff; }
.step-no { width:20px; height:20px; line-height:20px; margin:0 auto 4px; border-radius:50%; background:var(--ink); color:#fff; font-size:8.5pt; font-weight:700; }
.step-name { font-weight:800; font-size:9.5pt; }
.step-cmd { font-family:'SF Mono',Menlo,monospace; font-size:7.3pt; color:var(--blue); margin:2px 0; }
.step-desc { font-size:7.8pt; color:var(--ink2); }
.step-file { font-family:'SF Mono',Menlo,monospace; font-size:6.8pt; color:var(--muted); margin-top:4px; border-top:1px dashed var(--line); padding-top:3px; word-break:break-all; }
.split { display:flex; gap:10px; margin:10px 0; }
.side { flex:1; border-radius:8px; padding:10px 12px; }
.side.train { background:var(--blue-soft); } .side.test { border:1.5px solid var(--blue); }
.side-title { font-weight:800; margin-bottom:6px; font-size:9.5pt; }
.chip { display:inline-block; padding:2px 10px; margin:2px 4px 2px 0; border-radius:12px; background:var(--blue); color:#fff; font-weight:700; font-size:9pt; }
.chip.hollow { background:#fff; color:var(--ink); border:1.5px solid var(--blue); }
.card { border:1.5px solid var(--ink); border-radius:10px; padding:12px 14px; margin:10px 0; break-inside:avoid; font-size:9.2pt; }
.card-head { font-weight:800; font-size:10.5pt; padding-bottom:8px; border-bottom:1px solid var(--line); margin-bottom:8px; }
.card-id { font-family:'SF Mono',Menlo,monospace; font-size:8.5pt; background:var(--ink); color:#fff; padding:1px 6px; border-radius:4px; margin-right:6px; }
.badge { float:right; font-size:8.5pt; font-weight:700; background:var(--green-soft); padding:1px 8px; border-radius:10px; }
.card-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 16px; }
.card .k { font-size:8.3pt; color:var(--muted); font-weight:700; }
.card .hint { font-size:7.8pt; color:var(--muted); }
.card-list { margin:2px 0 0; }
table.mini { font-size:8.3pt; margin:4px 0; } table.mini th, table.mini td { padding:3px 6px; }
.checklist li { margin:6px 0; }
</style></head><body>
"""


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if chrome is None:
        raise SystemExit("Chrome/Chromium을 찾지 못했습니다. CHROME 환경 변수로 경로를 지정하세요.")
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="simcompare-walkthrough-") as tmp:
        work = Path(tmp)
        print("1) 파이프라인 실행", file=sys.stderr)
        run = run_pipeline(work)
        print("2) 그림 생성", file=sys.stderr)
        figures.build_all(work / "data", work / "out", HERE / "figures")
        print("3) HTML·PDF 생성", file=sys.stderr)
        html_path = HERE / "walkthrough.html"
        html_path.write_text(build_html(work, run), encoding="utf-8")
    pdf_path = ROOT / "docs/walkthrough.pdf"
    print_pdf(html_path, pdf_path)
    print(pdf_path)


if __name__ == "__main__":
    main()
