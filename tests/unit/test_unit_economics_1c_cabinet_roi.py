import json

import pytest
from pydantic import ValidationError

from app.dto.unit_economics_1c import (
    DEFAULT_TARGET_ROI_BY_CODE,
    UnitEconomics1CCabinetSettings,
    UnitEconomics1CCabinetSettingsRequest,
    UnitEconomics1CCabinetSettingsWebRequest,
)
from app.infrastructure.database import database_for_path
from app.repositories import schema
from app.repositories import unit_economics_1c as repository
from app.unit_economics_1c_target_prices import cabinet_target_roi

AUDIT = {
    "updated_at": "2026-09-09T10:00:00+03:00",
    "updated_by_user_id": 7,
    "updated_by_name": "Test Admin",
}


@pytest.mark.parametrize("request_type", [UnitEconomics1CCabinetSettingsRequest, UnitEconomics1CCabinetSettingsWebRequest])
def test_roi_defaults_and_partial_configuration(request_type):
    defaults = request_type()
    assert defaults.target_roi_by_code == DEFAULT_TARGET_ROI_BY_CODE
    customized = request_type(target_roi_by_code={"A": 22.5, "D": 0})
    assert customized.target_roi_by_code == {**DEFAULT_TARGET_ROI_BY_CODE, "A": 22.5}
    customized.target_roi_by_code["B"] = 42
    assert defaults.target_roi_by_code["B"] == 30
    assert request_type().target_roi_by_code["B"] == 30


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf"), None, "", "nan", 1_000_001])
@pytest.mark.parametrize("request_type", [UnitEconomics1CCabinetSettingsRequest, UnitEconomics1CCabinetSettingsWebRequest])
def test_roi_rejects_invalid_percentages(request_type, value):
    with pytest.raises(ValidationError):
        request_type(target_roi_by_code={"A": value})


def test_roi_rejects_unknown_configuration_codes():
    with pytest.raises(ValidationError):
        UnitEconomics1CCabinetSettingsWebRequest(target_roi_by_code={"unknown": 50})


def test_roi_resolution_normalizes_tags_and_preserves_legacy_fallback():
    cabinet = UnitEconomics1CCabinetSettings(store_slug="rimili", target_roi_percent=75)
    for code, roi in DEFAULT_TARGET_ROI_BY_CODE.items():
        assert cabinet_target_roi(cabinet, " " + code.lower() + " ") == roi
    for code in (None, "", "  ", "UNKNOWN"):
        assert cabinet_target_roi(cabinet, code) == 75


def test_roi_map_round_trip_and_legacy_save_preserve_cabinet_scope(database_path):
    saved = repository.save_cabinet_settings(
        "rimili",
        UnitEconomics1CCabinetSettingsWebRequest(
            target_roi_by_code={"A": 42.5, "B": 0}, target_roi_percent=75,
        ),
        **AUDIT,
    )
    assert saved.target_roi_by_code == {**DEFAULT_TARGET_ROI_BY_CODE, "A": 42.5, "B": 0}
    assert repository.get_cabinet_settings("tris").target_roi_by_code == DEFAULT_TARGET_ROI_BY_CODE
    legacy_saved = repository.save_cabinet_settings(
        "rimili", UnitEconomics1CCabinetSettingsWebRequest(acquiring_percent=4.2), **AUDIT,
    )
    assert legacy_saved.target_roi_by_code == saved.target_roi_by_code
    assert legacy_saved.target_roi_percent == 75
    assert legacy_saved.acquiring_percent == 4.2
    with database_for_path(database_path).connect() as connection:
        row = connection.execute(
            "SELECT target_roi_by_code FROM unit_economics_1c_cabinet_settings WHERE store_slug='rimili'"
        ).fetchone()
    assert json.loads(row["target_roi_by_code"]) == saved.target_roi_by_code


def test_roi_migration_is_repeatable_and_preserves_existing_values(database_path):
    old = repository.save_cabinet_settings(
        "rimili",
        UnitEconomics1CCabinetSettingsRequest(
            target_roi_percent=75, target_drr_percent=7, team_commission_percent=4.25,
        ),
        **AUDIT,
    )
    database = database_for_path(database_path)
    with database.connect() as connection:
        connection.execute("ALTER TABLE unit_economics_1c_cabinet_settings DROP COLUMN target_roi_by_code")
        connection.commit()
    schema._migrate_unit_economics_1c_cabinet_settings(database)
    schema._migrate_unit_economics_1c_cabinet_settings(database)
    migrated = repository.get_cabinet_settings("rimili")
    assert migrated == old
    assert migrated.target_roi_by_code == DEFAULT_TARGET_ROI_BY_CODE
    assert cabinet_target_roi(migrated, "") == 75
