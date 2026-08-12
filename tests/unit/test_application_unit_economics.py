from datetime import UTC, datetime
from unittest import mock

import pytest

from app.application.unit_economics import UnitEconomicsConfigurationService
from app.dto.unit_economics import (
    FulfillmentNames,
    FulfillmentRatesRequest,
    PersistedFulfillmentRate,
    PersistedFulfillmentRates,
)
from app.errors import UnitEconomicsValidationError

NOW = datetime(2026, 8, 12, tzinfo=UTC)


@pytest.fixture
def configuration_service():
    repository = mock.Mock()
    unit_of_work = mock.MagicMock()
    unit_of_work.__enter__.return_value = unit_of_work
    unit_of_work.repository = repository
    service = UnitEconomicsConfigurationService(lambda: unit_of_work, clock=lambda: NOW)
    return service, repository, unit_of_work


def request(*names: str) -> FulfillmentRatesRequest:
    return FulfillmentRatesRequest(
        rates=tuple({"name": name, "storage": 1, "accept": 2, "fulfillment": 3} for name in names)
    )


@pytest.mark.unit
def test_save_rates_validates_names_and_commits(configuration_service) -> None:
    service, repository, unit_of_work = configuration_service
    repository.fulfillment_names.return_value = FulfillmentNames(("One", "Two"))

    result = service.save_rates(request("One", "Two"))

    assert result.updated_at == NOW
    assert result.rates[0].storage == 1
    repository.save.assert_called_once()
    unit_of_work.commit.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("names", "message"),
    [
        (("One", "One"), "дважды"),
        (("One", "Unknown"), "Неизвестный"),
        (("One",), "все фулфилменты"),
    ],
)
def test_save_rates_rejects_invalid_name_sets(configuration_service, names, message) -> None:
    service, repository, unit_of_work = configuration_service
    repository.fulfillment_names.return_value = FulfillmentNames(("One", "Two"))

    with pytest.raises(UnitEconomicsValidationError, match=message):
        service.save_rates(request(*names))

    unit_of_work.commit.assert_not_called()


@pytest.mark.unit
def test_configuration_loads_typed_rates(configuration_service) -> None:
    service, repository, _unit_of_work = configuration_service
    repository.rates.return_value = PersistedFulfillmentRates(
        (PersistedFulfillmentRate(name="One", fulfillment_per_unit=3),)
    )
    from app.dto.stores import StoreCollection

    result = service.configuration(StoreCollection(()))

    assert result.fulfillments[0].fulfillment == 3
