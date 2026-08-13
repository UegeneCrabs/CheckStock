from app.infrastructure.database import _postgresql_parameters


def test_postgresql_parameter_conversion_skips_quoted_question_marks() -> None:
    statement = "SELECT '?', \"?\", value FROM sample WHERE first = ? AND second = ?"

    assert _postgresql_parameters(statement) == (
        "SELECT '?', \"?\", value FROM sample WHERE first = %s AND second = %s"
    )


def test_postgresql_parameter_conversion_handles_escaped_quotes() -> None:
    statement = "SELECT 'it''s ?' FROM sample WHERE value = ?"

    assert _postgresql_parameters(statement) == "SELECT 'it''s ?' FROM sample WHERE value = %s"
