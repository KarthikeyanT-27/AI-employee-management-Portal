"""Generate the employee attrition dataset used to train the Random Forest model.

No real HR data is shipped with this project, so a synthetic dataset is built
from documented, realistic relationships between the six input features and
attrition risk (low satisfaction, low pay, heavy overtime, poor attendance and
frequent leave raise risk; experience lowers it). Noise is added so the model
has to learn, not memorise. Replace employee_attrition.csv with a real dataset
that has the same columns and re-run train_model.py if you have one.
"""
import numpy as np
import pandas as pd

SEED = 42
N = 3000


def build(n: int = N, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "attendance_pct": np.clip(rng.normal(90, 8, n), 50, 100).round(1),
        "salary_level": rng.integers(1, 6, n),
        "overtime_hours": np.clip(rng.normal(15, 11, n), 0, 60).round(1),
        "years_experience": np.clip(rng.exponential(5, n), 0, 25).round(1),
        "job_satisfaction": np.clip(rng.normal(6.5, 2.0, n), 1, 10).round(1),
        "leave_frequency": np.clip(rng.poisson(6, n), 0, 24),
    })
    # latent attrition propensity
    z = (
        -0.45 * (df.job_satisfaction - 6.5)
        - 0.35 * (df.salary_level - 3)
        + 0.06 * (df.overtime_hours - 15)
        - 0.08 * (df.attendance_pct - 90)
        + 0.12 * (df.leave_frequency - 6)
        - 0.05 * (df.years_experience - 5)
        + 0.015 * (df.overtime_hours - 15) * (3 - df.salary_level)  # underpaid AND overworked
        + rng.normal(0, 0.5, n)
    )
    lo, hi = np.quantile(z, [0.50, 0.80])
    df["attrition_risk"] = np.where(z < lo, "Low", np.where(z < hi, "Moderate", "High"))
    return df


if __name__ == "__main__":
    data = build()
    data.to_csv("employee_attrition.csv", index=False)
    print(data.shape)
    print(data.attrition_risk.value_counts())
