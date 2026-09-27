# CrossChainEventCorroborator

A permissionless cross-chain event corroboration oracle. Anyone submits a claim describing a
transaction on a supported chain -- an NFT mint, a bridge lock, a bridge burn, a token transfer --
together with the parameters it should match and a small bounty. Any keeper may then trigger a
GenLayer consensus round that independently re-derives the answer from two or more fixed, trusted
block explorers per chain, never a caller-supplied URL. The resulting CONFIRMED / REJECTED state
is a reusable fact any other contract or off-chain integrator can read -- e.g. a bridge gating a
wrapped-token mint on `is_confirmed(claim_id)` instead of trusting a single relayer's word.

## Reviewer summary

- **Live app**: not included in this submission -- see "Scope" below.
- **Source**: part of this repository, under `cross-chain-event-corroborator/`.
- **Contract**: add StudioNet contract address here when deployed.
- **Main workflow**: anyone calls `submit_claim(chain, event_type, tx_hash, ...)` with an optional
  bounty -- the claim's id is derived from every asserted field, not just `(chain, tx_hash)`, so a
  bogus-parameter claim about a real transaction can never occupy that transaction's slot for the
  true submitter -- then any keeper calls `request_corroboration(claim_id)`, which asks a consensus
  round to independently classify each fixed explorer's account of the transaction -> code
  aggregates those classifications asymmetrically into CONFIRMED, REJECTED, or a retryable
  INSUFFICIENT -> a terminal round pays a capped keeper reward out of the bounty; whatever remains
  -- the whole bounty on a STALE claim, or the amount above the cap on a CONFIRMED/REJECTED one --
  is refundable to the submitter via `refund_remaining_bounty`. The exact same assertion may be
  resubmitted once its current attempt reaches any terminal state, always under a new, distinct id
  so the original terminal record is never mutated.

## Why this is a genuine Intelligent Contract, not just an oracle relayer

The simplest version of "did this cross-chain event really happen" is a trusted relayer (or a
multisig of them) who watches the source chain and posts an attestation -- no blockchain
intelligence required, and how a large share of production bridges work today. The problem that
design leaves unsolved is the single point of trust: one compromised or dishonest relayer can
fabricate an event that never happened, and a multisig only pushes the question to "who picked the
signers." `request_corroboration` replaces that with GenLayer's own consensus mechanism: every
validator independently renders the same fixed, public explorer pages and classifies them, and
`_aggregate` only returns a confident verdict when validators' code-gated classifications actually
agree -- no relayer key, no off-chain signature-gathering service, and no single party able to
unilaterally produce a CONFIRMED verdict.

## The asymmetric-risk design principle this contract is built around

CONFIRMED and REJECTED are not equally dangerous to get wrong. A downstream consumer is expected
to *act* on CONFIRMED as settled fact -- mint a wrapped token, release escrowed funds -- so a false
CONFIRMED is the severe, hard-to-reverse mistake. A false REJECTED merely blocks a claim that can
be resubmitted once understood; nothing was moved on the strength of it. Concretely:

1. **CONFIRMED requires every explorer configured for that chain to be fetchable and unanimously
   agree MATCH.** REJECTED only needs the smaller `MIN_SOURCES_REQUIRED` floor to be fetchable and
   unanimously negative. A source this contract never even managed to fetch cannot silently be
   treated as if it had agreed.
2. **A single `COULD_NOT_DETERMINE` anywhere blocks a confident answer in either direction.** The
   round simply stays INSUFFICIENT and is retryable, subject to a cooldown and an attempt cap.

## Architecture

- `contracts/CrossChainEventCorroborator.py` -- a single Intelligent Contract: permissionless
  claim submission and bounty funding, a bounded consensus round (`_consensus_corroborate`)
  against a fixed per-chain explorer registry (never a caller-supplied URL, so none of the
  DNS/redirect fetch-target hardening the rest of this series needs applies here -- see
  DECISION.md), code-level asymmetric aggregation (`_aggregate`), keeper rewards paid only on a
  genuine terminal verdict, and a stale-claim bounty refund path.
- `tests/direct/` -- direct-VM `gltest` tests covering claim submission and validation, bounty
  top-ups, the full CONFIRMED / REJECTED / INSUFFICIENT aggregation matrix (including the
  fetch-count gap that keeps CONFIRMED out of reach when a source was never fetched, and the
  single-`COULD_NOT_DETERMINE` case that blocks both directions), the cooldown, the attempt cap and
  the resulting STALE + refund path, and the keeper-reward payout on a terminal round.

### Contract methods

| Method | Kind | Consensus round? | What it does |
| --- | --- | --- | --- |
| `submit_claim(chain, event_type, tx_hash, contract_address, expected_from, expected_to, expected_token_id, expected_amount)` | payable write, permissionless | No | Opens a new claim for this exact assertion (every field feeds the id, not just `chain`/`tx_hash`) with an optional bounty. Blocked only while an identical assertion is already PENDING; a prior attempt that reached CONFIRMED, REJECTED, or STALE never blocks a fresh one, which always gets its own distinct id. |
| `add_bounty(claim_id)` | payable write, permissionless | No | Tops up a still-PENDING claim's bounty. |
| `request_corroboration(claim_id)` | write, permissionless | **Yes -- once per attempt** | Runs a consensus round against fixed explorer sources; moves the claim to CONFIRMED, REJECTED, stays PENDING, or goes STALE once attempts are exhausted. |
| `refund_remaining_bounty(claim_id)` | write, permissionless | No | Refunds a terminal claim's remaining bounty (all of it for STALE; whatever is left above the keeper's capped reward for CONFIRMED/REJECTED) to its original submitter. |
| `get_claim` / `is_confirmed` / `list_claims` | view | No | Reads -- the integration surface other contracts and integrators use. |

## Scope of this submission

This submission is **Contract + Tests**. A frontend (submit a claim, watch corroboration attempts,
integrate `is_confirmed` into another contract) is a natural next step but is not included here.

## Honest limitations

- **Page-rendering an explorer is not the same as querying its JSON API**, and some explorers
  rate-limit or challenge unauthenticated scraping. See DECISION.md's "Honest limitations" section
  for the production alternative (each explorer's own JSON API, or a public RPC provider) -- the
  consensus architecture does not change either way.
- **Only two independent explorer operators are configured per chain**, enough for redundancy
  against a single provider's outage but not against both configured operators sharing a common
  upstream bug.
- **No sybil resistance on who may submit a claim.** Anyone can submit a claim about any
  transaction; the contract only answers whether the evidence supports the claimed parameters, not
  whether the claim itself was worth asking. Integrators should look up their own `claim_id`s
  rather than trusting `list_claims` as a curated feed.
