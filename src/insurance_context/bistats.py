"""Statistics shared by the brief (in this process) and the analysis sandbox (whose kit carries this file's
source, so nothing here may import from the package): a forecast that says how wrong it has been, and how far
a value sits from its own history."""

import inspect
import math


def smooth(values, periods, season=None, repetitions=500):
    """Holt-Winters exponential smoothing of `values` (oldest first, one per period, no gaps), `periods` ahead,
    seasonal when `season` is given and there are two seasons of history. Backtests first: fitted without the
    last 3 values, how far off it was on them (miss, a share; nan when they are 0). Returns forecast, low and
    high (an 80% range from simulated paths), miss, and the method in words."""
    import numpy as np
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    y = np.asarray(values, dtype=float)
    seasonal = season if season and len(y) >= 2 * season else None

    def fit(v):
        return ExponentialSmoothing(v, trend="add", seasonal="add" if seasonal else None, seasonal_periods=seasonal,
                                    initialization_method="estimated").fit()
    back, actual = fit(y[:-3]).forecast(3), y[-3:]
    with np.errstate(divide="ignore", invalid="ignore"):
        miss = float(np.nanmean(np.abs(back - actual) / np.where(actual == 0, np.nan, np.abs(actual))))
    model = fit(y)
    # seeded: the same data gives the same range on every reload (a number that moves on its own is not one)
    seed = "rng" if "rng" in inspect.signature(model.simulate).parameters else "random_state"  # 0.15 renamed it
    sims = model.simulate(periods, repetitions=repetitions, error="add", **{seed: np.random.default_rng(0)})
    return {"forecast": [float(x) for x in model.forecast(periods)],
            "low": [float(x) for x in np.percentile(sims, 10, axis=1)],
            "high": [float(x) for x in np.percentile(sims, 90, axis=1)],
            "miss": miss, "method": "Holt-Winters exponential smoothing" + (" with a yearly season" if seasonal else "")}


def standing(history, value):
    """How far `value` sits from `history` (the periods before it): the usual (mean), how many standard
    deviations away (z; infinite when the history never moved and the value did) and the excess over usual."""
    n = len(history)
    mu = sum(history) / n
    sd = math.sqrt(sum((h - mu) ** 2 for h in history) / (n - 1)) if n > 1 else 0.0
    z = (value - mu) / sd if sd else (0.0 if value == mu else math.copysign(math.inf, value - mu))
    return {"usual": mu, "z": z, "excess": value - mu}


def t_tail(t, df):
    """P(|T| >= |t|) for Student's t with `df` degrees of freedom (Simpson's rule on the density; no scipy)."""
    t = abs(t)
    if not math.isfinite(t):
        return 0.0
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    f = lambda x: c * (1 + x * x / df) ** (-(df + 1) / 2)  # noqa: E731
    n = 400
    h = t / n
    area = f(0) + f(t) + sum((4 if i % 2 else 2) * f(i * h) for i in range(1, n))
    return max(0.0, 1 - 2 * area * h / 3)


def surprise(history, value):
    """How unlikely `value` is given `history` alone: the two-sided p-value of a new observation against a
    prediction interval from the history (Student's t, n - 1 degrees of freedom), which is honest about a
    usual estimated from only a year of months."""
    n = len(history)
    st = standing(history, value)
    if n < 3:
        return 1.0
    if not math.isfinite(st["z"]):
        return 0.0
    return t_tail(st["z"] / math.sqrt(1 + 1 / n), n - 1)
