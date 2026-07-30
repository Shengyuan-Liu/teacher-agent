import pytest

from app.services.structured_output import StructuredOutputError, parse_json_object


@pytest.mark.parametrize(
    ("candidate", "expected", "method"),
    [
        ('```json\n{"ok": true}\n```', {"ok": True}, "extracted_json"),
        ('result: {"ok": true,}', {"ok": True}, "removed_trailing_commas"),
        ("{'ok': True}", {"ok": True}, "python_literal"),
        (
            'prefix {"content": "a brace } inside a string", "ok": true} suffix',
            {"content": "a brace } inside a string", "ok": True},
            "extracted_json",
        ),
    ],
)
def test_parse_json_object_recovers_serialization_noise(candidate, expected, method):
    parsed = parse_json_object(candidate)
    assert parsed.value == expected
    assert parsed.recovery_method == method


def test_parse_json_object_rejects_non_objects():
    with pytest.raises(StructuredOutputError):
        parse_json_object("not structured at all")
