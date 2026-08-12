from unittest.mock import Mock

import pytest


@pytest.fixture
def marketplace_gateway() -> Mock:
    gateway = Mock()
    gateway.fetch.return_value = ()
    return gateway
