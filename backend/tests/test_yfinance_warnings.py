"""yfinance's own import puts a "show DeprecationWarning from ^yfinance" filter
in front of the app's ignore filter, so pandas' `inplace` deprecation warnings
flooded the terminal on every fetch. The provider re-asserts the ignore."""
import warnings

import pytest


def test_inplace_deprecation_from_yfinance_stays_quiet():
    pytest.importorskip("yfinance")
    from providers import yfinance as prov
    with warnings.catch_warnings():
        warnings.resetwarnings()
        # what yfinance does on import
        warnings.filterwarnings("default", category=DeprecationWarning, module="^yfinance")
        prov._yf()
        with warnings.catch_warnings(record=True) as seen:
            # record=True resets nothing but captures what would be shown
            warnings.filterwarnings("default", category=DeprecationWarning, module="^yfinance")
            prov._quiet_inplace_warnings()
            warnings.warn_explicit(
                "The inplace keyword in DataFrame.rename is deprecated and will be removed",
                DeprecationWarning, "yfinance/utils.py", 568, module="yfinance.utils")
            warnings.warn_explicit("something else is deprecated", DeprecationWarning,
                                   "yfinance/utils.py", 569, module="yfinance.utils")
        texts = [str(w.message) for w in seen]
        assert not any("inplace keyword" in t for t in texts)
        assert any("something else" in t for t in texts)      # only this message is silenced


def test_reasserting_is_idempotent():
    pytest.importorskip("yfinance")
    from providers import yfinance as prov
    with warnings.catch_warnings():
        prov._quiet_inplace_warnings()
        n = len(warnings.filters)
        prov._quiet_inplace_warnings()
        prov._quiet_inplace_warnings()
        assert len(warnings.filters) == n
