class DataRefreshError(RuntimeError):
    pass


class DataQualityError(DataRefreshError):
    pass
