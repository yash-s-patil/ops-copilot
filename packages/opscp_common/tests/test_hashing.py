import hashlib

from opscp_common.hashing import JsonValue, args_hash

_ARGS: dict[str, JsonValue] = {
    "order_id": 3912,
    "amount_minor": 890000,
    "reason_code": "lost_in_transit",
}


def test_hash_is_sha256_of_the_canonical_json() -> None:
    canonical = (
        b'{"args":{"amount_minor":890000,"order_id":3912,"reason_code":"lost_in_transit"},'
        b'"tool":"issue_refund"}'
    )

    assert args_hash("issue_refund", _ARGS) == hashlib.sha256(canonical).hexdigest()


def test_key_order_does_not_change_the_hash() -> None:
    assert args_hash("issue_refund", dict(reversed(_ARGS.items()))) == args_hash(
        "issue_refund", _ARGS
    )


def test_any_changed_argument_changes_the_hash() -> None:
    assert args_hash("issue_refund", {**_ARGS, "amount_minor": 890001}) != args_hash(
        "issue_refund", _ARGS
    )


def test_the_tool_name_is_part_of_the_hash() -> None:
    assert args_hash("update_ticket", _ARGS) != args_hash("issue_refund", _ARGS)


def test_non_ascii_text_is_hashed_as_utf8() -> None:
    canonical = '{"args":{"note":"धन्यवाद"},"tool":"update_ticket"}'.encode()

    assert args_hash("update_ticket", {"note": "धन्यवाद"}) == hashlib.sha256(canonical).hexdigest()
