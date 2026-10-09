class DataRefreshError(RuntimeError):
    pass


class DataSourceUnavailable(DataRefreshError):
    """No usable online provider is currently available; this is not a stock-level failure."""


class DataQualityError(DataRefreshError):
    pass
