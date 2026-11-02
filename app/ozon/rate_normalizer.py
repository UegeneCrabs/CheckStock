from datetime import datetime, timedelta

from app.core.constants import OZON_API_LIMITS, HTTPStatus, OzonRatePolicy
from app.dto.ozon_limits import OzonRateFeedback, OzonRateScope, OzonRateState


def reservation(scope: OzonRateScope, state: OzonRateState, now: datetime) -> OzonRateState:
    spacing = state.spacing if scope.policy == OzonRatePolicy.ADAPTIVE else scope.minimum_spacing
    return state.model_copy(update={"next_at": now + timedelta(seconds=spacing)})


def _seller_feedback(
    scope: OzonRateScope, state: OzonRateState, feedback: OzonRateFeedback, now: datetime
) -> OzonRateState:
    spacing, next_at = state.spacing, state.next_at
    if feedback.status == HTTPStatus.TOO_MANY_REQUESTS:
        spacing = max(
            scope.minimum_spacing,
            min(
                OZON_API_LIMITS.seller_maximum_backoff_seconds,
                max(spacing, OZON_API_LIMITS.minimum_retry_seconds) * 2,
            ),
        )
        delay = max(spacing, feedback.retry_seconds or 0)
        next_at = max(next_at, now + timedelta(seconds=delay))
    elif HTTPStatus.OK <= feedback.status < HTTPStatus.MULTIPLE_CHOICES:
        spacing = scope.minimum_spacing
        if feedback.remaining is not None and feedback.remaining <= 0:
            delay = (
                feedback.retry_seconds
                if feedback.retry_seconds is not None
                else OZON_API_LIMITS.minimum_retry_seconds
            )
            next_at = max(next_at, now + timedelta(seconds=max(scope.minimum_spacing, delay)))
    return OzonRateState(next_at=next_at, spacing=spacing)


def observe(
    scope: OzonRateScope, state: OzonRateState, feedback: OzonRateFeedback, now: datetime
) -> OzonRateState:
    if scope.policy == OzonRatePolicy.RETRY_AFTER:
        return _seller_feedback(scope, state, feedback, now)
    spacing, successes, next_at = state.spacing, state.successes, state.next_at
    if feedback.status == HTTPStatus.TOO_MANY_REQUESTS:
        spacing = max(
            scope.minimum_spacing,
            min(
                OZON_API_LIMITS.maximum_spacing_seconds,
                max(spacing, OZON_API_LIMITS.minimum_retry_seconds) * 2,
            ),
        )
        delay = max(spacing, feedback.retry_seconds or 0)
        next_at = max(next_at, now + timedelta(seconds=delay))
        successes = 0
    elif HTTPStatus.OK <= feedback.status < HTTPStatus.MULTIPLE_CHOICES:
        successes += 1
        if successes >= OZON_API_LIMITS.successes_before_decrease:
            spacing = max(scope.minimum_spacing, spacing * OZON_API_LIMITS.spacing_decrease_factor)
            successes = 0
        if feedback.remaining is not None and feedback.remaining <= 0:
            next_at = max(
                next_at,
                now
                + timedelta(
                    seconds=max(spacing, feedback.retry_seconds or OZON_API_LIMITS.minimum_retry_seconds)
                ),
            )
    return OzonRateState(next_at=next_at, spacing=spacing, successes=successes)
