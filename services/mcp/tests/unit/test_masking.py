import pytest

from opscp_mcp.services.masking import mask_email, mask_phone


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("priya@example.in", "p***@example.in"),
        ("ananya.k@example.in", "a***@example.in"),
        ("a@example.in", "***@example.in"),
        ("odd@name@example.in", "o***@example.in"),
        ("@example.in", "***"),
        ("not-an-email", "***"),
        ("", "***"),
    ],
)
def test_an_email_keeps_its_first_character_and_domain(email: str, masked: str) -> None:
    assert mask_email(email) == masked


@pytest.mark.parametrize(
    ("phone", "masked"),
    [
        ("+91 58200 11471", "+** ***** ***71"),
        ("5820011471", "********71"),
        ("071", "*71"),
        ("71", "**"),
        ("7", "*"),
        ("", ""),
        ("n/a", "***"),
        ("(022) 5820-1147", "(***) ****-**47"),
        ("+91 58200 11471 x12", "+** ***** ***** *12"),
        pytest.param(
            "+\u096f\u0967 \u096b\u096e\u0968\u0966\u0966 \u0967\u0967\u096a\u096d\u0967",
            "+** ***** ***\u096d\u0967",
            id="devanagari digits",
        ),
        pytest.param(
            "\uff19\uff18\uff17\uff16\uff15\uff14\uff13\uff12\uff11\uff10",
            "********\uff11\uff10",
            id="full-width digits",
        ),
        ("58200 11471 (alt: priya@example.in)", "***** ***71 (**** ****************)"),
    ],
)
def test_a_phone_number_keeps_its_layout_and_last_two_digits(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked
