from collections.abc import Callable, Coroutine

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.constants import WebErrorCode
from app.dto.integrations import WebFailure


class IntegrationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def sanitized(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                code = (
                    WebErrorCode.INVALID_CREDENTIAL
                    if "marketplace" in request.path_params
                    else WebErrorCode.INVALID_TARGET
                )
                return JSONResponse(WebFailure(code=code).model_dump(mode="json"), status_code=422)

        return sanitized
