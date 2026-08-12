class ApplicationError(Exception):
    pass


class StockValidationError(ApplicationError):
    pass


class UnitEconomicsValidationError(ApplicationError):
    pass
