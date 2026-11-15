"""Forecast recipes matched to the executed companion notebook."""
import warnings
import numpy as np
from statsmodels.tsa.holtwinters import SimpleExpSmoothing, Holt, ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

def predict(name, history, horizon):
    y = np.asarray(history, dtype=float)
    if not len(y) or not np.isfinite(y).all():
        raise ValueError('Finite historical sales are required.')
    fit = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        if name == 'Naive':
            pred = np.repeat(y[-1], horizon)
        elif name == 'SeasonalNaive5':
            pred = np.resize(y[-5:], horizon)
        elif name == 'MovingAverage5':
            past = list(y)
            pred = []
            for _ in range(horizon):
                value = float(np.mean(past[-5:]))
                pred.append(value)
                past.append(value)
        elif name == 'SES':
            fit = SimpleExpSmoothing(y, initialization_method='estimated').fit(optimized=True)
            pred = fit.forecast(horizon)
        elif name == 'Holt':
            fit = Holt(y, initialization_method='estimated').fit(optimized=True)
            pred = fit.forecast(horizon)
        elif name == 'HoltWintersAdd5':
            fit = ExponentialSmoothing(y, trend='add', seasonal='add', seasonal_periods=5, initialization_method='estimated').fit(optimized=True)
            pred = fit.forecast(horizon)
        elif name == 'SARIMA_1_0_0x1_0_0_5':
            fit = SARIMAX(y, order=(1, 0, 0), seasonal_order=(1, 0, 0, 5), enforce_stationarity=False, enforce_invertibility=False).fit(disp=False, maxiter=200)
            pred = fit.forecast(horizon)
        else:
            raise ValueError('Unsupported evaluated forecast recipe.')
    if any(('converg' in str(w.message).lower() for w in caught)):
        raise ValueError('Model fitting did not converge; no forecast saved.')
    ret = getattr(fit, 'mle_retvals', None)
    if ret is not None:
        ok = ret.get('converged', ret.get('success', True)) if isinstance(ret, dict) else getattr(ret, 'success', True)
        if not ok:
            raise ValueError('Model fitting did not converge; no forecast saved.')
    pred = np.asarray(pred, dtype=float)
    if len(pred) != horizon or not np.isfinite(pred).all():
        raise ValueError('Invalid forecast output.')
    return np.clip(pred, 0, None)
