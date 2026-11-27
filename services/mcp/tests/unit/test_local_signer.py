"""The approval token: what it says, and what is not accepted as one."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from joserfc import jwt
from joserfc.jwk import ECKey, RSAKey

from opscp_common.errors import BackendUnavailableError
from opscp_mcp.adapters.local_signer import LocalApprovalSigner, NoApprovalSigner
from opscp_mcp.ports.signing import ApprovalClaims
from opscp_mcp_testing.identities import ARJUN, KAVITA
from opscp_mcp_testing.signin import approval_key_pem
from opscp_mcp_testing.world import NOW

CLAIMS = ApprovalClaims(
    token_id=UUID(int=1),
    requester_oid=ARJUN.oid,
    approver_oid=KAVITA.oid,
    action_id=UUID(int=2),
    tool="issue_refund",
    args_hash="a" * 64,
    issued_at=NOW,
    expires_at=NOW + timedelta(seconds=120),
)


def _signer() -> LocalApprovalSigner:
    return LocalApprovalSigner(approval_key_pem())


def _parts(token: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    header, payload, signature = token.split(".")

    def decoded(segment: str) -> dict[str, Any]:
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

    return decoded(header), decoded(payload), signature


def _segment(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


async def test_a_token_says_who_asked_who_approved_and_exactly_what_for() -> None:
    header, payload, _ = _parts(await _signer().sign(CLAIMS))

    assert header["alg"] == "ES256"
    assert payload == {
        "iss": "opscp-mcp",
        "aud": "opscp-mcp/approval",
        "jti": str(UUID(int=1)),
        "sub": str(ARJUN.oid),
        "act": str(KAVITA.oid),
        "action_id": str(UUID(int=2)),
        "tool": "issue_refund",
        "args_hash": "a" * 64,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 120,
    }


async def test_a_token_is_read_back_exactly() -> None:
    signer = _signer()

    assert await signer.verify(await signer.sign(CLAIMS)) == CLAIMS


async def test_two_tokens_for_the_same_approval_are_told_apart_by_their_ids() -> None:
    signer = _signer()
    again = CLAIMS.model_copy(update={"token_id": UUID(int=9)})

    assert await signer.sign(CLAIMS) != await signer.sign(again)


async def _signed_by_another_key() -> str:
    other = ECKey.generate_key("P-256", private=True).as_pem(private=True).decode()
    return await LocalApprovalSigner(other).sign(CLAIMS)


async def _with_payload(**changed: Any) -> str:
    """A properly signed token whose claims differ from what the server writes."""
    _, payload, _ = _parts(await _signer().sign(CLAIMS))
    said = {name: value for name, value in (payload | changed).items() if value is not None}
    key = ECKey.import_key(approval_key_pem())
    return jwt.encode({"alg": "ES256"}, said, key, algorithms=["ES256"])


async def _payload_swapped() -> str:
    header, payload, signature = (await _signer().sign(CLAIMS)).split(".")
    _, other, _ = (await _with_payload(args_hash="b" * 64)).split(".")
    assert other != payload
    return f"{header}.{other}.{signature}"


async def _unsigned() -> str:
    _, payload, _ = _parts(await _signer().sign(CLAIMS))
    return f"{_segment({'alg': 'none'})}.{_segment(payload)}."


async def _signed_with_the_public_key_as_a_secret() -> str:
    public = ECKey.import_key(approval_key_pem()).as_pem(private=False)
    _, payload, _ = _parts(await _signer().sign(CLAIMS))
    signing_input = f"{_segment({'alg': 'HS256'})}.{_segment(payload)}"
    digest = hmac.new(public, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(digest).rstrip(b'=').decode()}"


NOT_OURS: dict[str, Any] = {
    "signed with another key": _signed_by_another_key,
    "payload swapped after signing": _payload_swapped,
    "not signed": _unsigned,
    "signed with the public key as a secret": _signed_with_the_public_key_as_a_secret,
    "another issuer": lambda: _with_payload(iss="someone-else"),
    "another audience": lambda: _with_payload(aud="opscp-mcp"),
    "no audience": lambda: _with_payload(aud=None),
    "no token id": lambda: _with_payload(jti=None),
    "a token id that is not one": lambda: _with_payload(jti="first"),
    "no approver": lambda: _with_payload(act=None),
    "no expiry": lambda: _with_payload(exp=None),
    "an expiry that is not a time": lambda: _with_payload(exp="soon"),
    "an expiry beyond any calendar": lambda: _with_payload(exp=10**20),
    "a tool that does not write": lambda: _with_payload(tool="get_order"),
    "no fingerprint": lambda: _with_payload(args_hash=None),
}


@pytest.mark.parametrize("forged", NOT_OURS.values(), ids=NOT_OURS.keys())
async def test_anything_else_is_not_an_approval(forged: Any) -> None:
    assert await _signer().verify(await forged()) is None


@pytest.mark.parametrize("text", ["", "not-a-token", "a.b", "a.b.c", "....", "é.é.é"])
async def test_text_that_is_not_a_token_is_not_an_approval(text: str) -> None:
    assert await _signer().verify(text) is None


async def test_an_expired_token_is_still_read_so_that_the_caller_can_judge_it() -> None:
    signer = _signer()
    old = CLAIMS.model_copy(update={"expires_at": NOW - timedelta(days=1)})

    assert await signer.verify(await signer.sign(old)) == old


@pytest.mark.parametrize(
    "key",
    [
        lambda: RSAKey.generate_key(2048, private=True).as_pem(private=True).decode(),
        lambda: ECKey.generate_key("P-384", private=True).as_pem(private=True).decode(),
        lambda: ECKey.import_key(approval_key_pem()).as_pem(private=False).decode(),
        lambda: "not a key",
    ],
    ids=["an RSA key", "another curve", "a public key", "not a key"],
)
def test_only_a_p256_private_key_signs_approvals(key: Any) -> None:
    with pytest.raises(ValueError, match="key"):
        LocalApprovalSigner(key())


async def test_without_a_key_approvals_cannot_be_signed_and_nothing_is_one() -> None:
    absent = NoApprovalSigner()

    with pytest.raises(BackendUnavailableError) as refused:
        await absent.sign(CLAIMS)

    assert refused.value.body().retryable
    assert await absent.verify(await _signer().sign(CLAIMS)) is None
