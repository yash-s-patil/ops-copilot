import time

import pytest

from opscp_common.redaction import redacted, redacted_text

CHARS = 200
EXAMINED = CHARS * 4 + 640


def _kept(arguments: dict[str, object]) -> dict[str, object]:
    return dict(redacted(arguments, arguments.keys(), CHARS))


def test_plain_arguments_are_kept_as_they_are() -> None:
    arguments: dict[str, object] = {
        "order_id": 4471,
        "events": "none",
        "include_notes": True,
        "cursor": None,
    }

    assert _kept(arguments) == arguments


def test_an_argument_the_tool_does_not_declare_is_left_out() -> None:
    arguments = {"order_id": 4471, "asha_rao_at_example_in": True, "phone_919000000001": 1}

    assert redacted(arguments, ["order_id", "events"], CHARS) == {"order_id": 4471}


@pytest.mark.parametrize(
    "contact",
    [
        "asha.rao@example.in",
        "Asha.Rao+orders@mail.example.co.in",
        "o'brien@example.com",
        "asha!rao#x@example.in",
        "asha.rao%40example.in",
        "asha\uff20example.in",
        "+91 90000 00001",
        "+91-90000-00001",
        "+91 - 90000 - 00001",
        "90000/00001",
        "0091,90000,00001",
        "022 5550 1234 56",
        "9000000001",
        "+91​9000000001",
    ],
)
def test_an_email_address_or_phone_number_inside_text_is_replaced_by_a_hash(contact: str) -> None:
    kept = redacted_text(f"refund for {contact} please", CHARS)

    assert kept.startswith("refund for <")
    assert kept.endswith("> please")
    assert kept.count("<") == 1


@pytest.mark.parametrize(
    "written",
    [
        '"asha rao"@example.in',
        "asha@localhost",
        "asha@[192.168.1.1]",
        "asha at example dot in",
        "not an address at all",
        "",
    ],
)
def test_an_argument_that_exists_to_carry_a_contact_is_hashed_whole(written: str) -> None:
    kept = _kept({"email": written, "phone": written})

    assert str(kept["email"]).startswith("<email:")
    assert str(kept["phone"]).startswith("<phone:")
    assert written == "" or written not in str(kept)


def test_the_same_contact_always_gives_the_same_hash_whatever_its_spelling() -> None:
    emails = _kept({"email": "Asha.Rao@Example.in", "query": "mail asha.rao%40example.in"})
    phones = [redacted_text(text, CHARS) for text in ("+91 90000 00001", "+919000000001")]

    assert emails["query"] == f"mail {emails['email']}"
    assert phones[0] == phones[1] != emails["email"]


@pytest.mark.parametrize(
    "text",
    [
        "order 4471 placed 2026-11-21 for INR 6,200, ticket 2207",
        "550e8400-e29b-41d4-a716-446655440000",
        "delivered 2026-11-26T10:15:00Z",
        "tracking AWB1234567890123IN",
        "cursor eyJpZCI6MTIzNDU2Nzg5MDEyfQ",
    ],
)
def test_an_identifier_or_a_date_is_not_mistaken_for_a_phone_number(text: str) -> None:
    assert redacted_text(text, CHARS) == text


@pytest.mark.parametrize("number", [9_000_000_001, 919_000_000_001, -9_000_000_001, 2**80])
def test_a_number_long_enough_to_be_a_phone_number_is_recorded_by_its_kind(number: int) -> None:
    assert _kept({"customer_id": number, "order_id": 999_999_999}) == {
        "customer_id": "<int>",
        "order_id": 999_999_999,
    }


def test_free_text_is_cut_to_the_limit() -> None:
    assert _kept({"query": "x" * 5000}) == {"query": "x" * CHARS}


@pytest.mark.parametrize("separator", [" ", ","])
def test_a_contact_cut_in_two_where_examination_stops_is_not_shown(separator: str) -> None:
    long_address = "a" * 60 + "@" + "b" * 60 + ".example.in"
    filler = (long_address + separator) * 12
    text = filler[: EXAMINED - 12] + "asha.rao@example.in and more"

    kept = redacted_text(text, CHARS)

    assert text[EXAMINED - 12 : EXAMINED] == "asha.rao@exa"
    assert "asha" not in kept
    assert "@" not in kept


def test_a_contact_at_the_very_end_of_a_short_text_is_still_hashed() -> None:
    assert redacted_text("write to asha.rao@example.in", CHARS).startswith("write to <email:")


def test_a_character_the_database_cannot_store_is_replaced() -> None:
    kept = _kept({"query": "carrier\x00exception", "email": "asha.rao@example.in\x00"})

    assert kept["query"] == "carrier�exception"
    assert "\x00" not in str(kept)


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ([1, 2], "<list>"),
        (1.5, "<float>"),
        (b"bytes", "<bytes>"),
    ],
)
def test_a_value_no_tool_takes_is_recorded_by_its_kind_only(value: object, kept: str) -> None:
    assert _kept({"order_id": value, "email": value}) == {"order_id": kept, "email": kept}


def test_a_very_long_text_is_handled_quickly() -> None:
    started = time.monotonic()

    kept = [redacted_text(text, CHARS) for text in ("a" * 4_000_000 + "@", "1 " * 2_000_000)]

    assert all(len(text) <= CHARS for text in kept)
    assert time.monotonic() - started < 1


def test_an_object_is_recorded_one_level_deep_with_the_same_care() -> None:
    recommendation = {
        "amount_minor": 62000,
        "reason_code": "goodwill_delay",
        "reason_text": "Customer asha.rao@example.in waited 97 hours",
        "nested": {"email": "asha.rao@example.in"},
        "not a name": 1,
    }

    kept = redacted({"recommendation": recommendation}, ["recommendation"], CHARS)["recommendation"]

    assert isinstance(kept, dict)
    assert (kept["amount_minor"], kept["reason_code"], kept["nested"]) == (
        62000,
        "goodwill_delay",
        "<dict>",
    )
    assert set(kept) == {"amount_minor", "reason_code", "reason_text", "nested"}
    assert "asha" not in str(kept)


@pytest.mark.parametrize(
    "sent", ["not-a-real-approval", {"jws": "not-a-real-approval"}, ["not-a-real-approval"], 7, ""]
)
def test_an_approval_is_never_recorded_whatever_shape_it_was_sent_in(sent: object) -> None:
    assert _kept({"approval_token": sent}) == {"approval_token": "<credential>"}
    assert _kept({"approval_token": None}) == {"approval_token": None}
