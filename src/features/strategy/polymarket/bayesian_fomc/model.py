"""Walk-forward logistic regression — FOMC 금리 결정 확률 추정.

scipy 만 사용 (sklearn 불필요).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

_FEATURES = ["cpi_yoy", "pce_yoy", "unrate", "fedfunds_ub"]


class _LogisticReg:
    def __init__(self, C: float = 2.0):
        self.C = C
        self._mean = self._std = self.coef_ = None
        self.intercept_ = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_LogisticReg":
        self._mean = X.mean(0)
        self._std  = X.std(0) + 1e-8
        Xn = (X - self._mean) / self._std

        def nll(p):
            w, b = p[:-1], p[-1]
            prob = np.clip(expit(Xn @ w + b), 1e-9, 1 - 1e-9)
            return -(y * np.log(prob) + (1 - y) * np.log(1 - prob)).sum() + (w**2).sum() / (2 * self.C)

        res = minimize(nll, np.zeros(X.shape[1] + 1), method="L-BFGS-B")
        self.coef_       = res.x[:-1]
        self.intercept_  = res.x[-1]
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return expit((X - self._mean) / self._std @ self.coef_ + self.intercept_)


class FOMCModel:
    """Walk-forward FOMC P(hike) 추정기."""

    def __init__(self, min_samples: int = 10, C: float = 2.0):
        self.min_samples = min_samples
        self.C = C
        self._hist: pd.DataFrame | None = None

    def load(self, df: pd.DataFrame) -> None:
        self._hist = df.copy()
        self._hist["meeting_date"] = pd.to_datetime(self._hist["meeting_date"])

    def predict(self, as_of: date, features: dict[str, float]) -> float | None:
        if self._hist is None:
            return None

        train = self._hist[self._hist["meeting_date"] < pd.Timestamp(as_of)]
        if len(train) < self.min_samples:
            return None

        X = train[_FEATURES].values.astype(float)
        y = (train["outcome"] == 1).astype(float).values

        if y.sum() == 0 or (1 - y).sum() == 0:
            return float(y.mean())

        mdl = _LogisticReg(C=self.C).fit(X, y)
        return float(mdl.predict_proba(np.array([[features[f] for f in _FEATURES]]))[0])
