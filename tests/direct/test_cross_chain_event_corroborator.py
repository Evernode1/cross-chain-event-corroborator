from datetime import datetime, timedelta, timezone

import pytest

from conftest import warp_to

GEN = 10**18

NOW = "2099-01-01T00:00:00Z"
TX = "0x" + "ab" * 32           # well-formed 0x + 64 hex chars
TX_2 = "0x" + "cd" * 32
CLAIM_ID = f"ethereum:{TX}"

ETHERSCAN_RE = r"https://etherscan\.io/tx/.*"
BLOCKSCOUT_RE = r"https://eth\.blockscout\.com/tx/.*"
CORROBORATION_PROMPT_RE = r".*independently verifying a claimed blockchain transaction.*"


def _iso_plus(iso: str, seconds: int) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def submit_claim(
    contract, direct_vm, sender, chain="ethereum", event_type="NFT_MINT", tx_hash=TX,
    contract_address="", expected_from="", expected_to="", expected_token_id="",
    expected_amount="", value=0,
):
    direct_vm.sender = sender
    direct_vm.value = value
    claim_id = contract.submit_claim(
        chain, event_type, tx_hash, contract_address, expected_from, expected_to,
        expected_token_id, expected_amount,
    )
    direct_vm.value = 0
    return claim_id


def mock_sources(direct_vm, etherscan_ok=True, blockscout_ok=True):
    direct_vm.clear_mocks()
    if etherscan_ok:
        direct_vm.mock_web(ETHERSCAN_RE, {"status": 200, "body": "etherscan tx detail page text"})
    if blockscout_ok:
        direct_vm.mock_web(BLOCKSCOUT_RE, {"status": 200, "body": "blockscout tx detail page text"})


def mock_verdict(direct_vm, etherscan="MATCH", blockscout="MATCH", reason="Evidence reviewed."):
    direct_vm.mock_llm(
        CORROBORATION_PROMPT_RE,
        f'{{"result_etherscan":"{etherscan}","result_blockscout":"{blockscout}","rationale":"{reason}"}}',
    )


def mock_round(direct_vm, etherscan_ok=True, blockscout_ok=True, etherscan="MATCH",
               blockscout="MATCH", reason="Evidence reviewed."):
    mock_sources(direct_vm, etherscan_ok=etherscan_ok, blockscout_ok=blockscout_ok)
    mock_verdict(direct_vm, etherscan=etherscan, blockscout=blockscout, reason=reason)


# --- claim submission ---

def test_submit_claim_creates_pending(contract, direct_vm, direct_bob):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    assert claim_id == CLAIM_ID
    c = contract.get_claim(claim_id)
    assert c["status"] == "PENDING"
    assert c["submitted_at"] == NOW
    assert c["check_attempts"] == 0
    assert c["bounty"] == "0"


def test_submit_claim_records_bounty(contract, direct_vm, direct_bob):
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=5 * GEN)
    assert contract.get_claim(claim_id)["bounty"] == str(5 * GEN)


def test_submit_claim_rejects_unsupported_chain(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_bob, chain="solana")


def test_submit_claim_rejects_bad_event_type(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_bob, event_type="MADE_UP_TYPE")


def test_submit_claim_rejects_malformed_tx_hash(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_bob, tx_hash="0xnothex")


def test_submit_claim_rejects_short_tx_hash(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_bob, tx_hash="0x1234")


def test_submit_claim_rejects_malformed_expected_address(contract, direct_vm, direct_bob):
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_bob, expected_from="not-an-address")


def test_submit_claim_accepts_empty_expected_fields(contract, direct_vm, direct_bob):
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    c = contract.get_claim(claim_id)
    assert c["expected_from"] == ""
    assert c["expected_amount"] == ""


def test_submit_claim_duplicate_fails(contract, direct_vm, direct_bob, direct_carol):
    submit_claim(contract, direct_vm, direct_bob)
    with pytest.raises(Exception):
        submit_claim(contract, direct_vm, direct_carol)


def test_submit_claim_same_tx_different_chain_is_a_separate_claim(contract, direct_vm, direct_bob):
    submit_claim(contract, direct_vm, direct_bob, chain="ethereum")
    claim_id_2 = submit_claim(contract, direct_vm, direct_bob, chain="polygon")
    assert claim_id_2 == f"polygon:{TX}"
    assert contract.get_claim(CLAIM_ID)["status"] == "PENDING"
    assert contract.get_claim(claim_id_2)["status"] == "PENDING"


# --- bounty top-ups ---

def test_add_bounty_tops_up(contract, direct_vm, direct_bob, direct_carol):
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=1 * GEN)
    direct_vm.sender = direct_carol
    direct_vm.value = 2 * GEN
    contract.add_bounty(claim_id)
    direct_vm.value = 0
    assert contract.get_claim(claim_id)["bounty"] == str(3 * GEN)


def test_add_bounty_rejects_zero(contract, direct_vm, direct_bob):
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    direct_vm.sender = direct_bob
    direct_vm.value = 0
    with pytest.raises(Exception):
        contract.add_bounty(claim_id)


def test_add_bounty_requires_existing_claim(contract, direct_vm, direct_bob):
    direct_vm.sender = direct_bob
    direct_vm.value = 1 * GEN
    with pytest.raises(Exception):
        contract.add_bounty("ethereum:" + "00" * 32)
    direct_vm.value = 0


# --- corroboration: the asymmetric aggregation matrix ---

def test_request_corroboration_confirmed_when_all_sources_match(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=1 * GEN)
    mock_round(direct_vm, etherscan="MATCH", blockscout="MATCH")
    direct_vm.sender = direct_dave
    result = contract.request_corroboration(claim_id)
    assert result == "CONFIRMED"
    c = contract.get_claim(claim_id)
    assert c["status"] == "CONFIRMED"
    assert c["confirmed_at"] == NOW
    assert contract.is_confirmed(claim_id) is True


def test_request_corroboration_rejected_when_all_sources_disagree(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan="NOT_FOUND", blockscout="MISMATCH")
    direct_vm.sender = direct_dave
    result = contract.request_corroboration(claim_id)
    assert result == "REJECTED"
    c = contract.get_claim(claim_id)
    assert c["status"] == "REJECTED"
    assert c["rejected_at"] == NOW
    assert contract.is_confirmed(claim_id) is False


def test_request_corroboration_insufficient_when_a_source_unfetchable_blocks_confirmed(
    contract, direct_vm, direct_bob, direct_dave
):
    """Even a single MATCH from the one source that could be fetched must NOT be enough for
    CONFIRMED -- confirming requires EVERY configured source to have actually been checked."""
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan_ok=True, blockscout_ok=False, etherscan="MATCH")
    direct_vm.sender = direct_dave
    result = contract.request_corroboration(claim_id)
    assert result == "INSUFFICIENT"
    assert contract.get_claim(claim_id)["status"] == "PENDING"


def test_request_corroboration_insufficient_on_single_could_not_determine(
    contract, direct_vm, direct_bob, direct_dave
):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
    direct_vm.sender = direct_dave
    result = contract.request_corroboration(claim_id)
    assert result == "INSUFFICIENT"
    assert contract.get_claim(claim_id)["status"] == "PENDING"


def test_request_corroboration_requires_pending_status(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan="MATCH", blockscout="MATCH")
    direct_vm.sender = direct_dave
    contract.request_corroboration(claim_id)
    with pytest.raises(Exception):
        contract.request_corroboration(claim_id)


def test_request_corroboration_requires_existing_claim(contract, direct_vm, direct_dave):
    direct_vm.sender = direct_dave
    with pytest.raises(Exception):
        contract.request_corroboration("ethereum:" + "11" * 32)


# --- cooldown and retry ---

def test_request_corroboration_cooldown_blocks_immediate_retry(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
    direct_vm.sender = direct_dave
    assert contract.request_corroboration(claim_id) == "INSUFFICIENT"
    warp_to(direct_vm, _iso_plus(NOW, 60))  # well under the 600s cooldown
    with pytest.raises(Exception):
        contract.request_corroboration(claim_id)


def test_request_corroboration_retry_succeeds_after_cooldown(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
    direct_vm.sender = direct_dave
    assert contract.request_corroboration(claim_id) == "INSUFFICIENT"

    t1 = _iso_plus(NOW, 601)
    warp_to(direct_vm, t1)
    mock_round(direct_vm, etherscan="MATCH", blockscout="MATCH")
    assert contract.request_corroboration(claim_id) == "CONFIRMED"
    assert contract.get_claim(claim_id)["confirmed_at"] == t1


# --- attempt cap, STALE, and refund ---

def test_request_corroboration_goes_stale_after_max_attempts(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=2 * GEN)
    t = NOW
    direct_vm.sender = direct_dave
    for i in range(5):  # MAX_CHECK_ATTEMPTS
        mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
        result = contract.request_corroboration(claim_id)
        t = _iso_plus(t, 601)
        warp_to(direct_vm, t)
        if i < 4:
            assert result == "INSUFFICIENT"
        else:
            assert result == "STALE"
    c = contract.get_claim(claim_id)
    assert c["status"] == "STALE"
    assert c["check_attempts"] == 5
    assert c["bounty"] == str(2 * GEN)  # untouched until refunded


def test_refund_stale_bounty(contract, direct_vm, direct_bob, direct_carol, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=2 * GEN)
    t = NOW
    direct_vm.sender = direct_dave
    for _ in range(5):
        mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
        contract.request_corroboration(claim_id)
        t = _iso_plus(t, 601)
        warp_to(direct_vm, t)
    assert contract.get_claim(claim_id)["status"] == "STALE"

    direct_vm.sender = direct_carol  # anyone may trigger the refund
    contract.refund_stale_bounty(claim_id)
    assert contract.get_claim(claim_id)["bounty"] == "0"


def test_refund_stale_bounty_requires_stale_status(contract, direct_vm, direct_bob, direct_carol):
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=1 * GEN)
    direct_vm.sender = direct_carol
    with pytest.raises(Exception):
        contract.refund_stale_bounty(claim_id)


def test_refund_stale_bounty_rejects_double_refund(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=1 * GEN)
    t = NOW
    direct_vm.sender = direct_dave
    for _ in range(5):
        mock_round(direct_vm, etherscan="MATCH", blockscout="COULD_NOT_DETERMINE")
        contract.request_corroboration(claim_id)
        t = _iso_plus(t, 601)
        warp_to(direct_vm, t)
    contract.refund_stale_bounty(claim_id)
    with pytest.raises(Exception):
        contract.refund_stale_bounty(claim_id)


# --- keeper reward ---

def test_keeper_reward_paid_on_confirmed(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=1 * GEN)  # well above the cap
    mock_round(direct_vm, etherscan="MATCH", blockscout="MATCH")
    direct_vm.sender = direct_dave
    contract.request_corroboration(claim_id)
    c = contract.get_claim(claim_id)
    # KEEPER_REWARD_CAP_WEI = 5 * 10**15 is paid out of the 1 GEN bounty; the remainder stays.
    assert int(c["bounty"]) == 1 * GEN - 5 * 10**15


def test_keeper_reward_capped_when_bounty_is_small(contract, direct_vm, direct_bob, direct_dave):
    warp_to(direct_vm, NOW)
    small_bounty = 10**12  # well under KEEPER_REWARD_CAP_WEI
    claim_id = submit_claim(contract, direct_vm, direct_bob, value=small_bounty)
    mock_round(direct_vm, etherscan="MATCH", blockscout="MATCH")
    direct_vm.sender = direct_dave
    contract.request_corroboration(claim_id)
    assert contract.get_claim(claim_id)["bounty"] == "0"  # the whole small bounty was paid out


# --- views ---

def test_list_claims(contract, direct_vm, direct_bob, direct_carol):
    submit_claim(contract, direct_vm, direct_bob, tx_hash=TX, chain="ethereum")
    submit_claim(contract, direct_vm, direct_carol, tx_hash=TX_2, chain="ethereum")
    listed = contract.list_claims(0, 10)
    assert len(listed) == 2


def test_is_confirmed_false_before_corroboration(contract, direct_vm, direct_bob):
    claim_id = submit_claim(contract, direct_vm, direct_bob)
    assert contract.is_confirmed(claim_id) is False


def test_is_confirmed_false_for_unknown_claim(contract, direct_vm):
    assert contract.is_confirmed("ethereum:" + "99" * 32) is False


def test_get_claim_requires_existing_claim(contract, direct_vm):
    with pytest.raises(Exception):
        contract.get_claim("ethereum:" + "22" * 32)
