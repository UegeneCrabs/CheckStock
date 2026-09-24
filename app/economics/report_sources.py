"""One read per source within a report, without sharing mutable data across requests."""


class ReportSources:
    def __init__(self):
        self._values = {}

    def read(self, loader, *args):
        key = (loader, args)
        if key not in self._values:
            self._values[key] = loader(*args)
        return self._values[key]
