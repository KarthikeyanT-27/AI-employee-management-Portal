"""Loads the saved Random Forest pipeline once and serves predictions."""
import json
import logging
from pathlib import Path

import joblib
import pandas as pd

log = logging.getLogger(__name__)


class AttritionModel:
    def __init__(self, model_path: str, meta_path: str):
        self.model_path, self.meta_path = Path(model_path), Path(meta_path)
        self.model = None
        self.meta = {}
        self.error = None
        self._load()

    def _load(self):
        try:
            self.model = joblib.load(self.model_path)
            self.meta = json.loads(self.meta_path.read_text())
            # The forest was trained with n_jobs=-1; spawning a thread pool per request is slower than
            # predicting in-process for the small batches the dashboard sends.
            self.model.named_steps["rf"].n_jobs = 1
            log.info("Attrition model loaded (%s)", self.model_path)
        except Exception as exc:  # noqa: BLE001 - surface a clear message in the API
            self.error = f"Could not load model: {exc}. Run `python ML/train_model.py` first."
            log.error(self.error)

    @property
    def ready(self) -> bool:
        return self.model is not None

    @property
    def features(self):
        return self.meta.get("features", [])

    def predict_many(self, feature_dicts: list[dict]) -> list[dict]:
        """Score many employees with ONE model call (vectorised)."""
        if not self.ready:
            raise RuntimeError(self.error)
        if not feature_dicts:
            return []
        frame = pd.DataFrame([[f[c] for c in self.features] for f in feature_dicts], columns=self.features)
        proba = self.model.predict_proba(frame)
        decoding = self.meta["decoding"]  # {"0": "Low", ...}
        names = [decoding[str(int(c))] for c in self.model.classes_]
        out = []
        for row in proba:
            probs = {n: round(float(p), 4) for n, p in zip(names, row)}
            out.append({"risk_label": max(probs, key=probs.get), "probabilities": probs})
        return out

    def predict(self, feats: dict) -> dict:
        return self.predict_many([feats])[0]

    def info(self) -> dict:
        m = self.meta
        return {"ready": self.ready, "error": self.error, "algorithm": "RandomForestClassifier",
                "features": m.get("features"), "metrics": m.get("metrics"),
                "feature_importance": m.get("feature_importance"),
                "best_params": m.get("best_params"), "sklearn_version": m.get("sklearn_version")}
