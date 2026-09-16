"""따라하기 문서(walkthrough)용 그림을 만든다.

build.py가 파이프라인 실행 결과 디렉터리(data/, out/)를 넘겨 호출한다.
그림은 SVG로 저장하고 글자는 문서를 여는 쪽 글꼴로 그린다(svg.fonttype=none).
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from PIL import Image  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402

# 색: 강조 3색(파랑·주황·청록) + 맥락용 회색. 글자는 series 색을 쓰지 않는다.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CONTEXT = "#b9b7b0"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8983"
GRID = "#e8e7e2"
SURFACE = "#ffffff"
SEQ = LinearSegmentedColormap.from_list("seq_blue", ["#ffffff", "#cde2fb", "#6da7ec", "#256abf", "#0d366b"])

FAILURE_LABEL = {"lateral_open": "끊김(open)", "lateral_short": "옆 전극 붙음(lateral short)",
                 "vertical_short": "위아래 전극 붙음(vertical short)"}
FAILURE_MARKER = {"lateral_open": "o", "lateral_short": "s", "vertical_short": "^"}


def _style() -> None:
    plt.rcParams.update({
        "font.family": ["Apple SD Gothic Neo", "AppleGothic", "DejaVu Sans"],
        "svg.fonttype": "none",
        "axes.unicode_minus": False,
        "font.size": 9,
        "axes.edgecolor": GRID,
        "axes.linewidth": 0.8,
        "axes.labelcolor": INK2,
        "axes.titlesize": 10,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "axes.titlelocation": "left",
        "axes.titlepad": 8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "legend.frameon": False,
        "legend.fontsize": 8.5,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
    })


def _save(fig, path: Path) -> None:
    fig.savefig(path, format="svg")
    plt.close(fig)


def _short(sample_id: str) -> str:
    product, version, _ = sample_id.split("-")
    return f"{product} {version}" if product == "P1" else product


# ---------------------------------------------------------------- 1. 입력 예시
def fig_inputs(data: Path, dst: Path) -> None:
    """P1 v1 옆 전극 붙음 사례: 전극 그림 2장 + 이물 크기별 hotspot 3장."""
    sid = "P1-v1-LSHORT"
    layouts = [np.load(data / f"arrays/{sid}-{s}.npy") for s in ("a", "b")]
    hots = [np.asarray(Image.open(data / f"images/{sid}-hot{k}.png"), dtype=float) / 255.0 for k in (2, 4, 8)]
    titles = ["전극 A (data 선)", "전극 B (화소 전극)", "hotspot · 이물 2 µm", "hotspot · 이물 4 µm", "hotspot · 이물 8 µm"]
    fig, axes = plt.subplots(1, 5, figsize=(7.4, 2.35))
    for i, (ax, img, title) in enumerate(zip(axes, layouts + hots, titles)):
        cmap = LinearSegmentedColormap.from_list("lay", ["#ffffff", "#3b3a37"]) if i < 2 else SEQ
        ax.imshow(img, cmap=cmap, vmin=0, vmax=1, interpolation="nearest")
        ax.set_title(title, fontsize=8.5, loc="center", fontweight="normal", color=INK2, pad=5)
        ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(True); sp.set_color(GRID)
    _save(fig, dst)


# ---------------------------------------------------------------- 2. Fab 분포
def fig_fab(data: Path, dst: Path) -> None:
    dists = json.load(open(data / "fab.json"))["distributions"]
    pick = {}
    for d in dists:
        if d["value_kind"] != "probability":
            continue
        if d.get("is_reference"):
            pick.setdefault("기준 분포(순위 비교용)", d)
        elif d["fab_id"] == "FAB_A" and d["process_layer"] == "M2" and d["product_family"] == "F1":
            pick["FAB_A (F1, M2 공정)"] = d
        elif d["fab_id"] == "FAB_B" and d["process_layer"] == "M2" and d["product_family"] == "F2":
            pick["FAB_B (F2, M2 공정)"] = d
    order = ["FAB_A (F1, M2 공정)", "FAB_B (F2, M2 공정)", "기준 분포(순위 비교용)"]
    colors = [BLUE, ORANGE, CONTEXT]
    first = pick[order[0]]
    labels = [f"{(lo + hi) / 2:g}" for lo, hi in zip(first["bin_lower"], first["bin_upper"])]
    x = np.arange(len(labels))
    width = 0.26
    fig, ax = plt.subplots(figsize=(5.6, 2.5))
    for i, (name, color) in enumerate(zip(order, colors)):
        vals = np.array(pick[name]["raw_values"], dtype=float)
        vals = vals / np.nansum(vals)
        ax.bar(x + (i - 1) * (width + 0.02), vals, width, color=color, label=name, zorder=3)
    ax.set_xticks(x, labels)
    ax.set_xlabel("이물 크기 구간 중앙값 (µm)")
    ax.set_ylabel("비율")
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, loc="upper right", bbox_to_anchor=(1.0, 1.18))
    _save(fig, dst)


# ---------------------------------------------------------------- 3. 데이터 준비 상태
def fig_readiness(data: Path, dst: Path) -> None:
    bundles = pd.read_csv(data / "bundles.csv")
    hot = pd.read_csv(data / "hotspots.csv")
    ca = pd.read_csv(data / "ca_records.csv")
    geom = json.load(open(data / "geometry.json"))
    cols = ["전극 그림", "hotspot 2", "hotspot 4", "hotspot 8", "CA 1", "CA 2", "CA 4", "CA 8", "CA 16", "실제 크기", "형상 수치"]
    products = sorted(bundles["product_id"].unique())
    grid = np.zeros((len(products), len(cols)))
    for r, p in enumerate(products):
        sids = bundles.loc[bundles["product_id"] == p, "sample_id"].tolist()
        row = [1.0]
        for k in (2.0, 4.0, 8.0):
            row.append(np.mean([((hot["sample_id"] == s) & (hot["particle_size"] == k)).any() for s in sids]))
        for k in (1.0, 2.0, 4.0, 8.0, 16.0):
            vals = []
            for s in sids:
                m = ca[(ca["sample_id"] == s) & (ca["particle_size"] == k)]
                vals.append(bool(len(m)) and pd.notna(m["ca_value"]).any())
            row.append(np.mean(vals))
        row.append(float(bundles.loc[bundles["product_id"] == p, "unit_width"].notna().all()))
        row.append(np.mean([bool((geom.get(s) or {}).get("shape_metrics")) for s in sids]))
        grid[r] = row
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    ax.imshow(grid, cmap=LinearSegmentedColormap.from_list("ok", ["#fbe3d9", "#cde2fb"]), vmin=0, vmax=1, aspect="auto")
    for r in range(len(products)):
        for c in range(len(cols)):
            txt = "있음" if grid[r, c] == 1 else ("없음" if grid[r, c] == 0 else "일부")
            ax.text(c, r, txt, ha="center", va="center", fontsize=8,
                    color=INK if grid[r, c] == 1 else "#8f2f0c", fontweight="normal" if grid[r, c] == 1 else "bold")
    ax.set_xticks(range(len(cols)), cols, fontsize=8)
    ax.set_yticks(range(len(products)), products)
    ax.xaxis.tick_top()
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(cols)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(products)), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    _save(fig, dst)


# ---------------------------------------------------------------- 4. 학습 손실
def fig_loss(out: Path, dst: Path) -> None:
    hist = pd.read_csv(out / "train_history.csv")
    names = [("layout", "전극 그림"), ("hotspot", "hotspot"), ("ca", "CA 곡선"), ("fab", "Fab 분포"), ("geometry", "형상 수치")]
    fig, axes = plt.subplots(1, 5, figsize=(7.4, 1.9), sharex=True)
    for ax, (key, name) in zip(axes, names):
        y = hist[key].to_numpy()
        ax.plot(hist["epoch"], y, color=BLUE, linewidth=1.6)
        ax.plot(hist["epoch"].iloc[-1], y[-1], "o", color=BLUE, markersize=4)
        ax.set_title(name, fontsize=9)
        ax.annotate(f"{y[0]:.2f} → {y[-1]:.2f}", xy=(0.98, 0.95), xycoords="axes fraction", ha="right", va="top",
                    fontsize=7.5, color=INK2)
        ax.set_ylim(0, max(y) * 1.25)
        ax.grid(axis="x", visible=False)
        ax.tick_params(labelsize=7)
        ax.set_xticks([1, 10, 20])
    axes[2].set_xlabel("학습 반복 횟수(epoch)")
    axes[0].set_ylabel("오차")
    fig.tight_layout(w_pad=1.0)
    _save(fig, dst)


# ---------------------------------------------------------------- 5. CA 곡선
def fig_ca(out: Path, dst: Path) -> None:
    risk = pd.read_csv(out / "risk_table.csv")
    sizes = [1, 2, 4, 8, 16]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))

    def curve(sid):
        row = risk[risk["sample_id"] == sid].iloc[0]
        return np.array([row.get(f"ca@{s}", np.nan) for s in sizes], dtype=float)

    ax = axes[0]
    ax.set_title("① 제품별 CA 곡선 (끊김 불량)")
    open_ids = risk.loc[risk["failure_type"] == "lateral_open", "sample_id"].tolist()
    emph = {"P5-v1-OPEN": ORANGE, "P2-v1-OPEN": BLUE, "P3-v1-OPEN": AQUA}
    for sid in open_ids:
        if sid in emph:
            continue
        ax.plot(sizes, curve(sid), color=CONTEXT, linewidth=1.1, marker="o", markersize=2.5, zorder=2)
    for sid, color in emph.items():
        y = curve(sid)
        ax.plot(sizes, y, color=color, linewidth=2, marker="o", markersize=4, zorder=3,
                markeredgecolor=SURFACE, markeredgewidth=1)
        last = np.where(~np.isnan(y))[0][-1]
        ax.annotate(_short(sid), xy=(sizes[last], y[last]), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK, fontweight="bold")
    ax.set_xscale("log", base=2); ax.set_yscale("log")
    ax.set_xticks(sizes, [str(s) for s in sizes])
    ax.set_xlabel("이물 크기 (µm)"); ax.set_ylabel("CA (0~1 위험값)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlim(0.8, 26)
    ax.legend(handles=[Line2D([], [], color=CONTEXT, lw=1.1, label="나머지 제품")], loc="upper left")

    ax = axes[1]
    ax.set_title("② 설계 변경 v1 → v2 (P1, 전극 간격 확대)")
    names = [("OPEN", "끊김"), ("LSHORT", "옆 전극 붙음"), ("VSHORT", "위아래 전극 붙음")]
    changes = []
    for code, _ in names:
        v1, v2 = curve(f"P1-v1-{code}"), curve(f"P1-v2-{code}")
        changes.append(100.0 * (np.nanmax(v2) - np.nanmax(v1)) / np.nanmax(v1))
    y = np.arange(len(names))
    ax.barh(y, changes, 0.5, color=BLUE, zorder=3)
    for i, c in enumerate(changes):
        ax.annotate(f"{c:.0f}%", xy=(c, i), xytext=(-4, 0), textcoords="offset points", ha="right", va="center",
                    fontsize=8.5, color=INK, fontweight="bold")
    ax.set_yticks(y, [n for _, n in names])
    ax.invert_yaxis()
    ax.set_xlim(min(changes) * 1.45, 0)
    ax.axvline(0, color=INK2, linewidth=0.8)
    ax.set_xlabel("가장 큰 CA의 변화율 (%) · 음수 = 줄어듦")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(w_pad=2.5)
    _save(fig, dst)


# ---------------------------------------------------------------- 6. 순위 비교
def fig_rank(out: Path, dst: Path) -> None:
    rank = pd.read_csv(out / "ranking.csv")
    df = rank[rank["failure_type"] == "lateral_open"].copy()
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
    panels = [
        ("① 크기 보정: CA 원값 → 면적당 CA", "rank_ca_max", "rank_ca_area_max", ["CA 원값", "면적당 CA"], {"P3-v1-OPEN": ORANGE, "P4-v1-OPEN": BLUE}),
        ("② Fab 반영: 기준 분포 → 실제 Fab", "rank_caa_reference", "rank_caa_actual", ["기준 분포 CAA", "실제 Fab CAA"], {"P4-v1-OPEN": BLUE, "P1-v1-OPEN": AQUA}),
    ]
    n = len(df)
    for ax, (title, left, right, xl, emph) in zip(axes, panels):
        ax.set_title(title)
        for _, row in df.iterrows():
            sid = row["sample_id"]
            color = emph.get(sid, CONTEXT)
            lw, z = (2.2, 3) if sid in emph else (1.1, 2)
            a, b = row[left], row[right]
            label = _short(sid)
            if pd.isna(b):
                ax.plot([0], [a], "o", color=color, markersize=5, zorder=z)
                ax.annotate(f"{label}*", xy=(0, a), xytext=(-8, 0), textcoords="offset points",
                            ha="right", va="center", fontsize=8, color=MUTED)
                ax.annotate(f"* {label}: 실제 크기 정보가 없어 면적당 CA를 계산하지 않음", xy=(0, -0.2), xycoords="axes fraction",
                            fontsize=7.5, color=MUTED, annotation_clip=False)
                continue
            ax.plot([0, 1], [a, b], color=color, linewidth=lw, marker="o", markersize=5, zorder=z,
                    markeredgecolor=SURFACE, markeredgewidth=1)
            weight = "bold" if sid in emph else "normal"
            ax.annotate(label, xy=(0, a), xytext=(-8, 0), textcoords="offset points", ha="right", va="center",
                        fontsize=8, color=INK if sid in emph else INK2, fontweight=weight)
            ax.annotate(f"{label}  {int(a)}위→{int(b)}위" if sid in emph else label, xy=(1, b), xytext=(8, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=8,
                        color=INK if sid in emph else INK2, fontweight=weight)
        ax.set_xlim(-0.35, 1.6)
        ax.set_ylim(n + 0.6, 0.4)
        ax.set_xticks([0, 1], xl)
        ax.set_yticks(range(1, n + 1), [f"{i}위" for i in range(1, n + 1)])
        ax.grid(axis="x", visible=False)
        ax.tick_params(axis="x", labelsize=8.5, labelcolor=INK)
    fig.tight_layout(w_pad=2)
    _save(fig, dst)


def _place_labels(fig, ax, xy, rows, emph) -> None:
    """점 라벨을 오른쪽 위부터 여덟 방향 중 다른 라벨·점과 겹치지 않는 자리에 둔다(픽셀 좌표 기준 근사)."""
    fig.canvas.draw()
    pts = ax.transData.transform(xy)
    placed = [(px - 5, py - 5, px + 5, py + 5) for px, py in pts]
    scale = fig.dpi / 72.0
    offsets = [(7, 3), (7, -12), (-7, 3), (-7, -12), (0, 9), (0, -17), (14, 12), (-14, -22), (20, -26), (-20, 16)]
    for (px, py), row in zip(pts, rows):
        product = row["product_id"]
        label = _short(row["sample_id"]) + ("" if row["split"] == "train" else " (평가)")
        width, height = len(label) * 5.4 * scale, 10 * scale
        best = offsets[0]
        for dx, dy in offsets:
            ha_left = dx >= 0 if dx != 0 else None
            x0 = px + dx * scale if ha_left else (px + dx * scale - width if ha_left is False else px - width / 2)
            box = (x0, py + dy * scale, x0 + width, py + dy * scale + height)
            if not any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in placed):
                best = (dx, dy)
                placed.append(box)
                break
        dx, dy = best
        ha = "left" if dx > 0 else ("right" if dx < 0 else "center")
        ax.annotate(label, xy=ax.transData.inverted().transform((px, py)), xytext=(dx, dy), textcoords="offset points",
                    ha=ha, va="bottom", fontsize=8.5, color=INK if product in emph else INK2,
                    fontweight="bold" if product in emph else "normal")


# ---------------------------------------------------------------- 7. 특징 지도
def fig_map(out: Path, dst: Path) -> None:
    """끊김 사례 7개를 두 공간에서 2차원 지도로 비교한다(좌표는 학습 행으로 맞춘 PCA)."""
    z = np.load(out / "embeddings.npz")
    meta = pd.read_csv(out / "embedding_meta.csv")
    train = (meta["split"] == "train").to_numpy()
    spaces = [("① 모양 정보만 사용 (shape_only)", z["shape_only"]),
              ("② 위험 수치까지 사용 (with_risk_ae)", z["with_risk_ae"])]
    rows = meta.index[meta["failure_type"] == "lateral_open"].to_numpy()
    fit_rows = rows[train[rows]]
    emph = {"P5": ORANGE, "P2": BLUE}
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
    for ax, (title, space) in zip(axes, spaces):
        xy = PCA(2).fit(space[fit_rows]).transform(space[rows])
        ax.set_title(title)
        for (x, yv), i in zip(xy, rows):
            row = meta.loc[i]
            product = row["product_id"]
            color = emph.get(product, CONTEXT)
            is_train = row["split"] == "train"
            ax.scatter(x, yv, s=60, facecolor=color if is_train else SURFACE, edgecolor=color if not is_train else SURFACE,
                       linewidth=1.6 if not is_train else 1.2, zorder=3 if product in emph else 2)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel("가로·세로축은 요약 좌표 (단위·의미 없음, 거리만 봄)", fontsize=7.5, color=MUTED)
        ax.margins(0.22)
        _place_labels(fig, ax, xy, [meta.loc[i] for i in rows], emph)
    handles = [Line2D([], [], lw=0, marker="o", mfc=CONTEXT, mec=CONTEXT, ms=7, label="채운 점 = 학습에 쓴 제품"),
               Line2D([], [], lw=0, marker="o", mfc=SURFACE, mec=CONTEXT, mew=1.6, ms=7, label="속 빈 점 = 평가용 제품")]
    fig.legend(handles=handles, ncol=2, loc="lower center", bbox_to_anchor=(0.5, -0.05))
    fig.tight_layout(rect=(0, 0.06, 1, 1), w_pad=2)
    _save(fig, dst)


# ---------------------------------------------------------------- 8. 제품 빼보기 안정성
def fig_loo(out: Path, dst: Path) -> None:
    loo = pd.read_csv(out / "loo_stability.csv")
    y = np.arange(len(loo))
    h = 0.36
    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    ax.barh(y - h / 2 - 0.02, loo["top_items_jaccard"], h, color=BLUE, label="위험 상위 목록 유지율", zorder=3)
    ax.barh(y + h / 2 + 0.02, loo["neighbor_jaccard"], h, color=ORANGE, label="비슷한 사례 목록 유지율", zorder=3)
    ax.set_yticks(y, [f"{p} 빼고" for p in loo["held_out_product"]])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.0)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0", "0.25", "0.5", "0.75", "1 (완전 유지)"])
    ax.grid(axis="y", visible=False)
    low = loo.loc[loo["neighbor_jaccard"].idxmin()]
    ax.annotate(f"{low['neighbor_jaccard']:.2f}", xy=(low["neighbor_jaccard"], loo.index[loo["held_out_product"] == low["held_out_product"]][0] + h / 2),
                xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=INK, fontweight="bold")
    ax.legend(ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    _save(fig, dst)


# ---------------------------------------------------------------- 9. 입력 구성 비교
def fig_ablation(out: Path, dst: Path) -> None:
    ab = json.load(open(out / "evaluation.json"))["ablation"]
    pairs = [("layout", "전극 그림", "image_only"), ("hotspot", "hotspot", "image_only"),
             ("ca", "CA 곡선", "numeric_only"), ("fab", "Fab 분포", "numeric_only"), ("geometry", "형상 수치", "numeric_only")]
    part = [ab[cfg]["test_losses"][k] for k, _, cfg in pairs]
    full = [ab["full"]["test_losses"][k] for k, _, _ in pairs]
    x = np.arange(len(pairs))
    w = 0.36
    fig, ax = plt.subplots(figsize=(6.2, 2.5))
    ax.bar(x - w / 2 - 0.02, part, w, color=CONTEXT, label="같은 종류 입력만 사용 (그림만 / 숫자만)", zorder=3)
    ax.bar(x + w / 2 + 0.02, full, w, color=BLUE, label="모든 입력 사용", zorder=3)
    for i, (a, b) in enumerate(zip(part, full)):
        ax.annotate(f"{a:.2f}", xy=(i - w / 2 - 0.02, a), xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7, color=INK2)
        ax.annotate(f"{b:.2f}", xy=(i + w / 2 + 0.02, b), xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7, color=INK)
    ax.set_xticks(x, [n for _, n, _ in pairs])
    ax.set_ylabel("평가 제품 복원 오차\n(낮을수록 잘 복원)")
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=2, loc="upper left", bbox_to_anchor=(0, 1.2))
    _save(fig, dst)


def build_all(data: Path, out: Path, fig_dir: Path) -> list[Path]:
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("inputs.svg", lambda p: fig_inputs(data, p)),
        ("fab.svg", lambda p: fig_fab(data, p)),
        ("readiness.svg", lambda p: fig_readiness(data, p)),
        ("loss.svg", lambda p: fig_loss(out, p)),
        ("ca.svg", lambda p: fig_ca(out, p)),
        ("rank.svg", lambda p: fig_rank(out, p)),
        ("map.svg", lambda p: fig_map(out, p)),
        ("loo.svg", lambda p: fig_loo(out, p)),
        ("ablation.svg", lambda p: fig_ablation(out, p)),
    ]
    paths = []
    for name, fn in jobs:
        path = fig_dir / name
        fn(path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    import sys

    run = Path(sys.argv[1])
    print(build_all(run / "data", run / "out", Path(sys.argv[2])))
