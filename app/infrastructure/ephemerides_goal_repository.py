from datetime import date

from app.core.constants import Marketplace
from app.dto.ephemerides_goal import WeeklyGoal, WeeklyGoalChange, WeeklyGoalState
from app.infrastructure.database import Database


class WeeklyGoalConflict(ValueError):
    def __init__(self, latest: WeeklyGoal) -> None:
        super().__init__("Цель уже изменена другим пользователем")
        self.latest = latest


class WeeklyGoalRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def for_weeks(
        self, stores: tuple[str, ...], *weeks: date
    ) -> dict[tuple[str, str, date], WeeklyGoalState]:
        if not stores or not weeks:
            return {}
        marks = ",".join("?" for _ in stores)
        week_marks = ",".join("?" for _ in weeks)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT store_slug, article, week, quantity, version, applied_quantity "
                f"FROM ephemerides_weekly_goals WHERE store_slug IN ({marks}) AND week IN ({week_marks})",
                (*stores, *(week.isoformat() for week in weeks)),
            ).fetchall()
        return {
            (str(row["store_slug"]), str(row["article"]), date.fromisoformat(str(row["week"]))): (
                WeeklyGoalState.model_validate(
                    {field: row[field] for field in ("quantity", "version", "applied_quantity")}
                )
            )
            for row in rows
        }

    def apply(self, stores: tuple[str, ...], week: date) -> int:
        if not stores:
            return 0
        marks = ",".join("?" for _ in stores)
        with self.database.connect() as connection:
            result = connection.execute(
                f"""
                UPDATE ephemerides_weekly_goals AS goal
                   SET applied_quantity=quantity
                 WHERE goal.store_slug IN ({marks}) AND goal.week=?
                   AND (goal.quantity <> goal.applied_quantity
                        OR (goal.quantity IS NULL) <> (goal.applied_quantity IS NULL))
                   AND EXISTS (
                       SELECT 1 FROM stock_items AS product
                        WHERE product.store_slug=goal.store_slug AND product.article=goal.article
                          AND product.marketplace=? AND product.is_service=0
                   )
                """,
                (*stores, week.isoformat(), Marketplace.WB),
            )
            connection.commit()
        return result.rowcount

    def save(self, change: WeeklyGoalChange) -> WeeklyGoal:
        key = (change.store, change.article, change.week.isoformat())
        goal = WeeklyGoal(quantity=change.quantity, version=change.version + 1)
        with self.database.connect() as connection:
            if change.version == 0:
                result = connection.execute(
                    "INSERT INTO ephemerides_weekly_goals (store_slug, article, week, quantity, version) "
                    "VALUES (?,?,?,?,?) ON CONFLICT (store_slug, week, article) DO NOTHING",
                    (*key, goal.quantity, goal.version),
                )
            else:
                result = connection.execute(
                    "UPDATE ephemerides_weekly_goals SET quantity=?, version=? "
                    "WHERE store_slug=? AND article=? AND week=? AND version=?",
                    (goal.quantity, goal.version, *key, change.version),
                )
            if result.rowcount == 1:
                connection.commit()
                return goal
            row = connection.execute(
                "SELECT quantity, version FROM ephemerides_weekly_goals "
                "WHERE store_slug=? AND article=? AND week=?",
                key,
            ).fetchone()
            if row is not None:
                latest = WeeklyGoal.model_validate(dict(row))
                if latest.quantity == change.quantity:
                    return latest
                raise WeeklyGoalConflict(latest)
            raise WeeklyGoalConflict(WeeklyGoal(quantity=None, version=0))
