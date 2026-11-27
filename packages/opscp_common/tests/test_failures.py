import pytest

from opscp_common.failures import chain_of, failure_site
from opscp_common.redaction import contacts_in, without_contacts

QUOTED = "ananya.k@example.in refused a refund of 62000"


def _fails_twice() -> None:
    try:
        raise KeyError(QUOTED)
    except KeyError as first:
        raise RuntimeError("wrapped " + QUOTED) from first


def test_a_failure_is_logged_by_kind_and_place_and_never_by_its_text() -> None:
    with pytest.raises(RuntimeError) as raised:
        _fails_twice()

    site = failure_site(raised.value)

    assert site["error"] == "KeyError"
    assert str(site["where"]).endswith("in _fails_twice']")
    assert QUOTED not in str(site)
    assert "ananya" not in str(site)


def test_the_chain_runs_from_the_outermost_failure_to_the_first() -> None:
    with pytest.raises(RuntimeError) as raised:
        _fails_twice()

    assert [type(error).__name__ for error in chain_of(raised.value)] == [
        "RuntimeError",
        "KeyError",
    ]


def test_a_chain_that_loops_is_cut_short() -> None:
    first, second = ValueError("a"), ValueError("b")
    first.__cause__, second.__cause__ = second, first

    assert len(chain_of(first)) == 8


def test_a_failure_that_was_never_raised_has_no_place() -> None:
    assert failure_site(ValueError("x")) == {"error": "ValueError", "where": []}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Write to Ananya.K@Example.in today", "ananya.k@example.in"),
        ("call +91 58200 11471", "915820011471"),
        ("call 91-58200-11471 or mail a%40b.example", "915820011471 a@b.example"),
    ],
)
def test_the_same_contact_details_are_found_however_they_are_written(
    first: str, second: str
) -> None:
    assert contacts_in(first) == contacts_in(second) != frozenset()


def test_text_with_no_contact_details_has_none() -> None:
    assert contacts_in("Order 4471 was packed for 96 hours, INR 620.00.") == frozenset()


def test_contact_details_are_hidden_and_the_rest_of_a_text_is_kept_whole() -> None:
    long = "The customer ananya.k@example.in (+91 58200 11471) was not told. " + "x" * 500

    hidden = without_contacts(long)

    assert hidden.startswith("The customer <email:")
    assert "<phone:" in hidden
    assert "ananya" not in hidden
    assert "58200" not in hidden
    assert hidden.endswith("x" * 500)
    assert without_contacts("Order 4471, INR 620.00.") == "Order 4471, INR 620.00."
