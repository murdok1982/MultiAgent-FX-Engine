"""
agents/ml_predictor.py — Predictive ML Agent (XGBoost + optional LSTM)
======================================================================

Predicts the directional sign of the next-bar log return on EUR/USD 1h.

Features (deterministic, computed from OHLCV):
  • returns(1, 3, 5, 10)        — multi-horizon returns
  • rsi_14, rsi_5
  • macd_hist
  • atr_14_norm                  — ATR / close
  • bb_position, bb_width
  • range_pct, body_pct          — candle shape
  • sma_dist_50, sma_dist_200    — normalized distance from SMA
  • hour_of_day, day_of_week     — seasonality

Targets:
  • y = 1 if next-bar return > 0 else 0

Two model heads:
  1. XGBoost classifier (default) — works without GPU, trains in seconds.
  2. LSTM (if torch is installed) — sequence of last 32 bars → next-bar sign.

A model artefact is cached at ./models/ml_predictor.joblib. The agent
trains on demand if no artefact exists, using yfinance historical data.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from data.data_feed import DataFeed, MarketSnapshot


MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
XGB_PATH = MODEL_DIR / "ml_predictor_xgb.joblib"
LSTM_PATH = MODEL_DIR / "ml_predictor_lstm.pt"


# ── Schema ────────────────────────────────────────────────────────────────────

class MLPrediction(BaseModel):
    bias: str = Field(default="NEUTRAL", description="BUY | SELL | NEUTRAL")
    prob_up: float = 0.5
    confidence: float = 0.0
    model_used: str = "none"        # xgboost | lstm | none
    expected_return_bps: float = 0.0
    feature_summary: str = ""

    @field_validator("bias")
    @classmethod
    def _b(cls, v):
        v = v.upper()
        return v if v in ("BUY", "SELL", "NEUTRAL") else "NEUTRAL"


# ── Feature Engineering ───────────────────────────────────────────────────────

def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute feature DataFrame aligned to df.index (drops initial NaNs)."""
    out = pd.DataFrame(index=df.index)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    op = df["open"].astype(float)

    # Returns
    log_r = np.log(close / close.shift(1))
    out["ret_1"] = log_r
    out["ret_3"] = log_r.rolling(3).sum()
    out["ret_5"] = log_r.rolling(5).sum()
    out["ret_10"] = log_r.rolling(10).sum()

    # RSI
    def _rsi(s, p):
        d = s.diff()
        g = d.clip(lower=0).rolling(p).mean()
        l_ = (-d.clip(upper=0)).rolling(p).mean()
        rs = g / l_.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    out["rsi_14"] = _rsi(close, 14)
    out["rsi_5"] = _rsi(close, 5)

    # MACD histogram
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = macd - macd_signal

    # ATR / normalized
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    out["atr_norm"] = atr / close

    # Bollinger
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std(ddof=0)
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    width = (upper - lower).replace(0, np.nan)
    out["bb_pos"] = (close - lower) / width
    out["bb_width"] = width / sma20

    # Candle shape
    out["range_pct"] = (high - low) / close
    out["body_pct"] = (close - op).abs() / close

    # SMA distance
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    out["sma50_dist"] = (close - sma50) / sma50
    out["sma200_dist"] = (close - sma200) / sma200

    # Seasonality
    try:
        idx = df.index
        out["hour"] = idx.hour / 23.0
        out["dow"] = idx.dayofweek / 6.0
    except Exception:
        out["hour"] = 0.5
        out["dow"] = 0.5

    return out.dropna()


# ── XGBoost head ──────────────────────────────────────────────────────────────

class _XGBHead:
    """Lazy-loaded XGBoost classifier wrapper."""

    def __init__(self):
        self._model = None
        self._feature_names: list[str] = []

    def available(self) -> bool:
        try:
            import xgboost  # noqa
            return True
        except ImportError:
            return False

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if not XGB_PATH.exists():
            return False
        try:
            import joblib
            blob = joblib.load(XGB_PATH)
            self._model = blob["model"]
            self._feature_names = blob["features"]
            return True
        except Exception:
            return False

    def train(self, df: pd.DataFrame) -> bool:
        """Train on the given OHLCV DataFrame. Returns True on success."""
        try:
            import xgboost as xgb
            import joblib
        except ImportError:
            return False

        feats = _build_features(df)
        if len(feats) < 200:
            return False
        # Target = sign of next-bar return
        y = (feats["ret_1"].shift(-1) > 0).astype(int)
        X = feats.iloc[:-1]
        y = y.iloc[:-1].astype(int)

        split = int(len(X) * 0.8)
        X_train, X_val = X.iloc[:split], X.iloc[split:]
        y_train, y_val = y.iloc[:split], y.iloc[split:]

        model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=1.0,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            n_jobs=2,
        )
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        self._model = model
        self._feature_names = list(X.columns)
        joblib.dump({"model": model, "features": self._feature_names}, XGB_PATH)
        return True

    def predict_proba(self, feats: pd.DataFrame) -> Optional[float]:
        if not self._load():
            return None
        try:
            row = feats[self._feature_names].iloc[[-1]]
            p = float(self._model.predict_proba(row)[0, 1])
            return p
        except Exception:
            return None


# ── LSTM head (optional) ──────────────────────────────────────────────────────

class _LSTMHead:
    """PyTorch LSTM sequence model. Skipped silently if torch missing."""

    SEQ_LEN = 32

    def __init__(self):
        self._model = None
        self._feature_names: list[str] = []

    def available(self) -> bool:
        try:
            import torch  # noqa
            return True
        except ImportError:
            return False

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if not LSTM_PATH.exists():
            return False
        try:
            import torch
            blob = torch.load(LSTM_PATH, map_location="cpu", weights_only=False)
            self._model = blob["model"]
            self._model.eval()
            self._feature_names = blob["features"]
            return True
        except Exception:
            return False

    def train(self, df: pd.DataFrame) -> bool:
        try:
            import torch
            import torch.nn as nn
            from torch.utils.data import DataLoader, TensorDataset
        except ImportError:
            return False

        feats = _build_features(df)
        if len(feats) < 500:
            return False
        feat_cols = [c for c in feats.columns]
        y_full = (feats["ret_1"].shift(-1) > 0).astype(int).to_numpy()[:-1]
        X_full = feats[feat_cols].to_numpy()[:-1]

        # Build sequences
        seqs, labels = [], []
        for i in range(self.SEQ_LEN, len(X_full)):
            seqs.append(X_full[i - self.SEQ_LEN: i])
            labels.append(y_full[i])
        if not seqs:
            return False
        X = torch.tensor(np.array(seqs), dtype=torch.float32)
        Y = torch.tensor(np.array(labels), dtype=torch.float32)

        split = int(len(X) * 0.8)
        train_ds = TensorDataset(X[:split], Y[:split])
        loader = DataLoader(train_ds, batch_size=64, shuffle=True)

        class LSTMNet(nn.Module):
            def __init__(self, n_feat):
                super().__init__()
                self.lstm = nn.LSTM(n_feat, 32, batch_first=True, num_layers=1)
                self.fc = nn.Linear(32, 1)

            def forward(self, x):
                out, _ = self.lstm(x)
                return torch.sigmoid(self.fc(out[:, -1, :])).squeeze(-1)

        net = LSTMNet(len(feat_cols))
        opt = torch.optim.Adam(net.parameters(), lr=1e-3)
        crit = nn.BCELoss()

        net.train()
        for epoch in range(8):
            for xb, yb in loader:
                opt.zero_grad()
                pred = net(xb)
                loss = crit(pred, yb)
                loss.backward()
                opt.step()
        net.eval()
        torch.save({"model": net, "features": feat_cols}, LSTM_PATH)
        self._model = net
        self._feature_names = feat_cols
        return True

    def predict_proba(self, feats: pd.DataFrame) -> Optional[float]:
        if not self._load():
            return None
        try:
            import torch
            row = feats[self._feature_names].iloc[-self.SEQ_LEN:].to_numpy()
            if len(row) < self.SEQ_LEN:
                return None
            x = torch.tensor(row[None, ...], dtype=torch.float32)
            with torch.no_grad():
                p = float(self._model(x).item())
            return p
        except Exception:
            return None


# ── Agent ─────────────────────────────────────────────────────────────────────

class MLPredictorAgent(BaseAgent):
    name = "MLPredictorAgent"

    def __init__(self, config=None, prefer_lstm: bool = False):
        super().__init__(config)
        self.model = "xgboost+lstm"
        self._feed = DataFeed()
        self._xgb = _XGBHead()
        self._lstm = _LSTMHead()
        self._prefer_lstm = prefer_lstm

    def _default_output(self) -> MLPrediction:
        return MLPrediction(feature_summary="ML predictor unavailable")

    async def analyze(self, snapshot: Optional[MarketSnapshot] = None, **kw) -> MLPrediction:
        if snapshot is None:
            snapshot = self._feed.get_snapshot()
        df = self._feed.get_ohlcv_dataframe("1h")
        if df.empty or len(df) < 200:
            return MLPrediction(feature_summary="Insufficient data")

        feats = _build_features(df)
        if feats.empty:
            return MLPrediction(feature_summary="No features")

        # First-run training (best effort)
        if self._xgb.available() and not XGB_PATH.exists():
            self.logger.info("Training XGBoost on bootstrap data...")
            self._xgb.train(df)

        # Inference
        p_xgb = self._xgb.predict_proba(feats) if self._xgb.available() else None
        p_lstm = self._lstm.predict_proba(feats) if self._lstm.available() else None

        if self._prefer_lstm and p_lstm is not None:
            p_up = p_lstm
            model_used = "lstm"
        elif p_xgb is not None:
            p_up = p_xgb
            model_used = "xgboost"
        elif p_lstm is not None:
            p_up = p_lstm
            model_used = "lstm"
        else:
            return MLPrediction(
                bias="NEUTRAL", prob_up=0.5, confidence=0.0,
                model_used="none",
                feature_summary="No model artefact and no library available",
            )

        # Threshold to bias
        margin = abs(p_up - 0.5)
        if p_up >= 0.58:
            bias = "BUY"
        elif p_up <= 0.42:
            bias = "SELL"
        else:
            bias = "NEUTRAL"
        confidence = float(min(1.0, margin * 2.5))

        # Expected return (bps) crude estimate from training-time
        # avg absolute next-bar log return assumed ≈ 8bps for 1h EURUSD
        exp_bps = float((p_up - 0.5) * 16.0)

        summary = (
            f"p_up={p_up:.3f} ({model_used}), "
            f"rsi14={feats['rsi_14'].iloc[-1]:.1f}, "
            f"macd_h={feats['macd_hist'].iloc[-1]:+.6f}, "
            f"bb_pos={feats['bb_pos'].iloc[-1]:.2f}"
        )
        self.logger.info(f"ML: bias={bias} conf={confidence:.2f} | {summary}")

        return MLPrediction(
            bias=bias,
            prob_up=round(p_up, 4),
            confidence=round(confidence, 3),
            model_used=model_used,
            expected_return_bps=round(exp_bps, 2),
            feature_summary=summary,
        )
