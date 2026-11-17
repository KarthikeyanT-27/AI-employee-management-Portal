"""Train, evaluate and save the Random Forest attrition classifier.

Run:  python train_model.py
Outputs: attrition_rf.joblib (sklearn Pipeline) and model_meta.json
"""
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline

HERE = Path(__file__).resolve().parent
FEATURES = ["attendance_pct", "salary_level", "overtime_hours",
            "years_experience", "job_satisfaction", "leave_frequency"]
TARGET = "attrition_risk"
LABEL_ENCODING = {"Low": 0, "Moderate": 1, "High": 2}   # ordinal target encoding
DECODING = {v: k for k, v in LABEL_ENCODING.items()}


def load_and_clean(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.drop_duplicates()
    df = df.dropna(subset=[TARGET])
    df = df[df[TARGET].isin(LABEL_ENCODING)]
    # sanity ranges (guards against bad rows in a replaced dataset)
    df = df[df.attendance_pct.between(0, 100) | df.attendance_pct.isna()]
    return df


def main():
    csv = HERE / "employee_attrition.csv"
    if not csv.exists():
        from generate_dataset import build
        build().to_csv(csv, index=False)
    df = load_and_clean(csv)
    X = df[FEATURES]
    y = df[TARGET].map(LABEL_ENCODING)

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)

    pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),          # missing-value handling
        ("rf", RandomForestClassifier(random_state=42, class_weight="balanced", n_jobs=-1)),
    ])
    grid = {"rf__n_estimators": [200, 400], "rf__max_depth": [8, 12, None],
            "rf__min_samples_leaf": [1, 3, 5]}
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    search = GridSearchCV(pipe, grid, cv=cv, scoring="f1_macro", n_jobs=-1)
    search.fit(X_tr, y_tr)
    model = search.best_estimator_

    pred = model.predict(X_te)
    acc = accuracy_score(y_te, pred)
    f1 = f1_score(y_te, pred, average="macro")
    cv_scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
    report = classification_report(y_te, pred, target_names=list(LABEL_ENCODING), output_dict=True)
    cm = confusion_matrix(y_te, pred).tolist()
    importances = dict(zip(FEATURES, (float(v) for v in model.named_steps["rf"].feature_importances_)))

    print(f"Best params : {search.best_params_}")
    print(f"Test accuracy: {acc:.3f} | macro-F1: {f1:.3f} | 5-fold CV acc: {cv_scores.mean():.3f} +/- {cv_scores.std():.3f}")
    print(classification_report(y_te, pred, target_names=list(LABEL_ENCODING)))
    print("Confusion matrix:", cm)

    joblib.dump(model, HERE / "attrition_rf.joblib", compress=3)
    meta = {
        "features": FEATURES,
        "label_encoding": LABEL_ENCODING,
        "decoding": {str(k): v for k, v in DECODING.items()},
        "metrics": {"accuracy": round(acc, 4), "macro_f1": round(f1, 4),
                    "cv_accuracy_mean": round(float(cv_scores.mean()), 4),
                    "cv_accuracy_std": round(float(cv_scores.std()), 4),
                    "confusion_matrix": cm, "report": report},
        "best_params": {k: (v if v is not None else "None") for k, v in search.best_params_.items()},
        "feature_importance": importances,
        "train_rows": int(len(X_tr)), "test_rows": int(len(X_te)),
        "sklearn_version": sklearn.__version__, "python": sys.version.split()[0],
    }
    (HERE / "model_meta.json").write_text(json.dumps(meta, indent=2))
    print("Saved attrition_rf.joblib and model_meta.json")


if __name__ == "__main__":
    main()
