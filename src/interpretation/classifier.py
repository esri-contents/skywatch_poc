"""조각 분류기: 건물 레이어를 약한 정답으로 '지붕·구조물 조각' 확률을 학습한다.

판독_코드는 판독 요소별 임계값(밝기 표준편차 ≤ 4, 대비 ≥ 10 ...)을 165필지 기준 판독에 맞춰
손으로 정했고, 같은 자료로 맞추고 같은 자료로 평가했다. 여기서는 같은 판독 요소를 입력으로 두되
판정 경계를 자료에서 학습하고, 학습과 평가를 공간적으로 분리한다.

- 정답: 건물 레이어(VWorld 도로명주소 건물)와 80% 이상 겹치는 조각 = 1, 레이어에서 2m 이상
  떨어진 조각 = 0, 그 사이(경계에 걸친 조각)는 학습에서 뺀다. 레이어에 없는 창고·가설물은
  0으로 들어가므로, 이 정답으로 잰 정밀도는 실제보다 낮게 나온다(육안 판독으로 따로 확인).
- 공간 블록 교차검증: block_m 격자 블록을 묶음으로 GroupKFold. 두 시기의 같은 위치는 같은
  블록이라 같은 fold에 들어간다. 모든 조각의 확률은 자기 블록을 학습하지 않은 모델의
  예측(out-of-fold)이므로 평가와 적용 모두 학습 자료 누수가 없다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from .obia import FEATURES

DERIVED = ["L_rel", "log_area"]
MODEL_FEATURES = FEATURES + DERIVED
POS_FP_FRAC = 0.8


def prepare(frames: dict[str, pd.DataFrame], block_m: float) -> pd.DataFrame:
    """시기별 조각 표를 합치고 파생 특징·정답·공간 블록을 붙인다."""
    parts = []
    for epoch, f in frames.items():
        f = f.copy()
        f["epoch"] = epoch
        # 시기 간 전역 밝기 차(2024가 +17~22 밝음)를 줄이기 위해 타일 안 상대 밝기를 둔다.
        f["L_rel"] = f["L"] - f.groupby("tile_id")["L"].transform("median")
        parts.append(f)
    d = pd.concat(parts, ignore_index=True)
    d["log_area"] = np.log10(d["area_m2"])
    d["target"] = np.where(d["fp_frac"] >= POS_FP_FRAC, 1, np.where(d["fp_near"] == 0, 0, -1))
    d["block"] = (np.floor(d["x"] / block_m).astype(int).astype(str) + "_"
                  + np.floor(d["y"] / block_m).astype(int).astype(str))
    return d


def legacy_rule(d: pd.DataFrame) -> pd.Series:
    """판독_코드 config.RULE의 '있음/불확실' 조건(지붕면·윤곽·면적)을 같은 특징으로 옮긴 기준선."""
    roof = (d["exg"] <= 0.01) & (d["L_std"] <= 4) & (d["contrast"].abs() >= 10)
    shape = d["rect_fill"].between(0.65, 1.3) & (d["solidity"] >= 0.75) & (d["elong"] <= 4)
    return roof & shape & (d["area_m2"] >= 12)


def _model(seed: int) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
                                          min_samples_leaf=40, l2_regularization=1.0, random_state=seed)


def fit_oof(d: pd.DataFrame, folds: int = 5, seed: int = 0, features: list[str] | None = None) -> tuple[np.ndarray, dict]:
    """공간 블록 교차검증으로 모든 조각의 out-of-fold 구조물 확률을 낸다."""
    features = features or MODEL_FEATURES
    X = d[features].to_numpy(np.float32)
    lab = d["target"].to_numpy() >= 0
    y = d["target"].to_numpy()
    w = np.sqrt(d["area_m2"].to_numpy())  # 큰 조각일수록 필지 면적 집계에 크게 기여
    prob = np.zeros(len(d), np.float32)
    fold_of = np.zeros(len(d), int)
    importances = []
    for k, (_, test_blocks) in enumerate(GroupKFold(folds).split(np.zeros(d["block"].nunique()),
                                                                groups=np.arange(d["block"].nunique()))):
        blocks = d["block"].unique()[test_blocks]
        te = d["block"].isin(blocks).to_numpy()
        tr = ~te & lab
        m = _model(seed + k).fit(X[tr], y[tr], sample_weight=w[tr])
        prob[te] = m.predict_proba(X[te])[:, 1]
        fold_of[te] = k
        if k == 0:
            s = np.flatnonzero(te & lab)
            s = np.random.default_rng(seed).choice(s, min(20000, len(s)), replace=False)
            pi = permutation_importance(m, X[s], y[s], scoring="average_precision", n_repeats=3, random_state=seed)
            importances = sorted(zip(features, pi.importances_mean), key=lambda t: -t[1])
    yl, pl = y[lab], prob[lab]
    rule = legacy_rule(d).to_numpy()[lab]
    metrics = {
        "segments": int(len(d)), "labeled": int(lab.sum()), "positive": int((yl == 1).sum()),
        "auc": float(roc_auc_score(yl, pl)), "average_precision": float(average_precision_score(yl, pl)),
        "positive_rate": float((yl == 1).mean()),
        "legacy_rule": {"precision": float(yl[rule].mean()) if rule.any() else None,
                        "recall": float(rule[yl == 1].mean()), "flagged": int(rule.sum())},
        "by_epoch": {e: {"auc": float(roc_auc_score(y[lab & (d["epoch"] == e).to_numpy()],
                                                    prob[lab & (d["epoch"] == e).to_numpy()]))}
                     for e in d["epoch"].unique()},
        "importance_top": [(n, round(float(v), 4)) for n, v in importances[:10]],
    }
    return prob, metrics


CONTEXT = ["nb_mean", "nb_max", "nb_wmean", "nb_share_hi"]


def adjacency(seg_path, chunk_rows: int = 2048) -> pd.DataFrame:
    """조각 라벨 래스터에서 맞닿은 조각 쌍과 맞닿은 화소 수(양방향). 열: a, b, n."""
    import rasterio
    parts = []
    with rasterio.open(seg_path) as ds:
        h = ds.height
        for r0 in range(0, h, chunk_rows):
            win = rasterio.windows.Window(0, r0, ds.width, min(chunk_rows + 1, h - r0))
            a = ds.read(1, window=win).astype(np.int64)
            for x, y in [(a[:, :-1], a[:, 1:]), (a[:-1], a[1:])]:
                m = (x != y) & (x > 0) & (y > 0)
                parts.append(pd.DataFrame({"a": x[m], "b": y[m]}))
    p = pd.concat(parts, ignore_index=True)
    p = pd.concat([p, p.rename(columns={"a": "b", "b": "a"})], ignore_index=True)
    return p.groupby(["a", "b"]).size().rename("n").reset_index()


def context_features(d: pd.DataFrame, adj: dict[str, pd.DataFrame], prob: np.ndarray, hi: float = 0.6) -> pd.DataFrame:
    """이웃 조각의 1단계 확률 요약(auto-context): 평균, 최대, 맞닿은 길이 가중 평균, 높은 확률 이웃의 경계 비율.

    큰 지붕이 용마루·골판 무늬로 여러 조각에 갈리면 일부 조각만 높은 확률을 받는다. 이웃 확률을
    두 번째 단계의 입력으로 주면 같은 지붕의 나머지 조각도 끌어올릴 수 있다(Tu & Bai, 2010).
    """
    out = pd.DataFrame(0.0, index=d.index, columns=CONTEXT)
    for epoch, a in adj.items():
        sel = (d["epoch"] == epoch).to_numpy()
        pmap = pd.Series(prob[sel], index=d.loc[sel, "seg_id"].to_numpy())
        e = a.assign(p=pmap.reindex(a["b"].to_numpy()).to_numpy()).dropna(subset=["p"])
        e["pn"] = e["p"] * e["n"]
        e["hn"] = (e["p"] >= hi) * e["n"]
        g = e.groupby("a").agg(nb_mean=("p", "mean"), nb_max=("p", "max"), pn=("pn", "sum"), hn=("hn", "sum"), n=("n", "sum"))
        g["nb_wmean"] = g["pn"] / g["n"]
        g["nb_share_hi"] = g["hn"] / g["n"]
        out.loc[sel, CONTEXT] = g.reindex(d.loc[sel, "seg_id"].to_numpy())[CONTEXT].fillna(0.0).to_numpy()
    return out


def fit_oof_context(d: pd.DataFrame, prob1: np.ndarray, adj: dict[str, pd.DataFrame], folds: int = 5,
                    seed: int = 0) -> tuple[np.ndarray, dict]:
    """2단계 auto-context: 1단계 out-of-fold 확률의 이웃 요약을 특징에 더해 같은 공간 블록 fold로 다시 학습한다.

    이웃 조각이 다른 블록에 있으면 그 이웃의 1단계 확률은 이 조각의 블록을 학습한 모델에서 나왔을 수
    있다(블록 경계에서만 생기는 약한 누수). 판단은 공간 교차검증 지표와 육안 검수 새 회차로 한다.
    """
    ctx = context_features(d, adj, prob1)
    return fit_oof(pd.concat([d, ctx], axis=1), folds, seed, features=MODEL_FEATURES + CONTEXT)


def threshold_table(d: pd.DataFrame, prob: np.ndarray, grid=None) -> pd.DataFrame:
    """정답이 있는 조각에서 임계값별 정밀도·재현율(면적 가중)."""
    grid = grid if grid is not None else np.round(np.arange(0.2, 0.91, 0.05), 2)
    lab = d["target"].to_numpy() >= 0
    y, p, a = d["target"].to_numpy()[lab], prob[lab], d["area_m2"].to_numpy()[lab]
    rows = []
    for t in grid:
        hit = p >= t
        tp = a[hit & (y == 1)].sum()
        rows.append({"threshold": t, "precision_area": tp / max(a[hit].sum(), 1e-9),
                     "recall_area": tp / max(a[y == 1].sum(), 1e-9),
                     "precision_count": (y[hit] == 1).mean() if hit.any() else np.nan,
                     "recall_count": hit[y == 1].mean()})
    out = pd.DataFrame(rows)
    out["f1_area"] = 2 * out.precision_area * out.recall_area / (out.precision_area + out.recall_area)
    return out
