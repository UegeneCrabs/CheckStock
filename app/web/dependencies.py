from typing import Annotated

from fastapi import Depends, Request

from app.application.identity import IdentityService
from app.application.stock import StockMovementService
from app.container import ApplicationContainer
from app.dto.identity import User


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container


def get_identity_service(
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> IdentityService:
    return container.identity


def get_current_user(request: Request) -> User:
    return request.state.user


def get_stock_movement_service(
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> StockMovementService:
    return container.stock


ContainerDependency = Annotated[ApplicationContainer, Depends(get_container)]
IdentityServiceDependency = Annotated[IdentityService, Depends(get_identity_service)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]
StockMovementServiceDependency = Annotated[
    StockMovementService,
    Depends(get_stock_movement_service),
]
