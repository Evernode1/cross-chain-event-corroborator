# CrossChainEventCorroborator Decision Record

## The product

A permissionless cross-chain event corroboration oracle. Anyone submits a claim -- "this tx_hash
on this chain is an NFT mint / bridge lock / bridge burn / transfer with these parameters" -- with
a small bounty attached. Any keeper may then ask a GenLayer consensus round to independently
re-derive the answer from fixed, trusted block explorers, and the resulting CONFIRMED/REJECTED
state becomes a reusable fact any other contract (a bridge, a marketplace, a DAO) can read.

## Counterfactual: why not just trust a relayer, or a multisig of relayers

The simplest version of "did event X happen on chain Y" is a single trusted relayer who watches
the source chain and posts an attestation -- this is how a large fraction of production bridges
actually work today, and it needs no blockchain intelligence at all. The problem is exactly the
single point of failure that shape implies: one compromised or lying relayer can fabricate an
event that never happened. A k-of-n multisig of relayers helps, but only pushes the trust question
to "who picked the n signers" and still requires an off-chain coordination process to gather
signatures. What this contract adds is a way to derive the same answer from public, independently
operated block explorers through GenLayer's own consensus mechanism -- no relayer's private key,
no off-chain signature-gathering service, and no single party who could unilaterally fabricate a
CONFIRMED verdict, because every validator in the round must independently reach the same
classification against the same public evidence for consensus to be reached at all.

## Why the two terminal outcomes have deliberately different bars, not just "ambiguity stays open"

Every consensus round in this series shares the instinct that an ambiguous result should stay
unresolved rather than be forced to a confident answer. This contract sharpens that instinct
further, and asymmetrically, because CONFIRMED and REJECTED are not equally dangerous to get
wrong. A downstream consumer -- a bridge minting a wrapped token, an escrow releasing funds, a
marketplace crediting a purchase -- is expected to *act* on CONFIRMED as settled fact. A false
CONFIRMED is therefore the severe failure mode: it can directly enable a fabricated cross-chain
event to move real value. A false REJECTED is a real cost too (it blocks a legitimate claim), but
it is a *recoverable* one -- REJECTED and the retryable INSUFFICIENT state look identical to a
downstream consumer (both mean "don't proceed"), and a wrongly rejected claim can simply be
resubmitted once the underlying mismatch is understood; nothing was moved or minted on the strength
of it. That asymmetry is why `_aggregate` requires *every* explorer this contract has configured
for a chain to be fetchable and unanimously MATCH before returning CONFIRMED, while REJECTED only
needs the smaller `MIN_SOURCES_REQUIRED` floor to be fetchable and unanimously negative. Both sides
still refuse to resolve past a single `COULD_NOT_DETERMINE` -- that part is symmetric, and shared
with every other consensus round in this series -- only the *fetchable-source-count* bar differs
between the two directions.

## Why explorer hosts are fixed literals, never a caller-supplied URL

ProofOfLifeVault, ContentAuthenticityOracle, and ReputationAttestor all let an owner register a
URL the contract will later fetch, which is exactly the shape that needs DNS-resolution re-checks,
redirect-status refusal, and a private/reserved/metadata-IP denylist -- a caller could otherwise
point the contract's own outbound fetch at an internal address. This contract has no equivalent
surface: `CHAIN_EXPLORERS` is a fixed dict of (explorer name, URL template) pairs chosen by this
contract's own code, keyed only by `source_chain`, and the only caller-influenced value that ever
reaches a URL is `tx_hash` -- interpolated into the *path*, never the host, and validated to a
strict `0x` + 64 lowercase-hex character set before it can reach a template at all
(`_require_hex_hash`). There is no SSRF surface here to harden against the way the other contracts'
life-signal/evidence URLs need, so this contract deliberately does not carry that machinery over --
adding DNS/redirect checks to a fetch whose host was never caller-influenced would be the same
needless-cost mistake ProofOfLifeVault's own v1.1 self-review caught and fixed on its GitHub fetch.

## Why CONFIRMED requires every configured source, not just a floor

An earlier draft used the same `MIN_SOURCES_REQUIRED` floor for both directions. That treats "two
out of two possible explorers agreed" and "two out of three possible explorers agreed, one was
simply never checked" as equally strong evidence for CONFIRMED, which they are not -- the second
case means a source that could have contradicted the claim was silently skipped. Requiring
`len(fetchable) == expected_count` for CONFIRMED closes that gap: a chain configured with two
explorers needs both; a chain configured with three would need all three. This makes CONFIRMED
strictly *harder* to reach as this contract adds more explorers per chain over time, which is the
correct direction -- more independent witnesses should raise the bar for the outcome downstream
consumers treat as final, not lower it.

## Why there is no admin

Every configuration decision here -- which chain, which event type, which expected parameters, how
large a bounty -- belongs to whoever submitted that specific claim, and nothing about one claim's
configuration touches another claim's outcome or economics. The only genuinely shared parameters
(`CHAIN_EXPLORERS`, the cooldown, the attempt cap, the keeper reward cap) are protocol-wide
constants applying identically to every claim, the same category of thing ProofOfLifeVault already
established needs no owner: there is no safety margin here an admin could selectively misconfigure
for one party without it being equally visible, and equally fixed, for everyone else.

## Why a STALE claim refunds the submitter instead of retrying forever

`MAX_CHECK_ATTEMPTS` exists because some claims are genuinely unresolvable through this contract's
evidence sources -- a transaction that only one of two configured explorers has indexed yet, or
that both render in a way the model repeatedly cannot parse. Retrying such a claim forever would
lock its bounty indefinitely and invite spam (submit garbage, force keepers to spend gas retrying
it out of good faith). Capping attempts and routing an exhausted claim's remaining bounty back to
its submitter -- not to whichever keeper happened to make the final, still-inconclusive attempt --
keeps the incentive honest: a keeper is only ever paid for producing an actual terminal verdict,
never for exhausting a claim's retry budget.

## Honest limitations, stated plainly rather than glossed over

- **Rendering a public explorer page is not the same as querying its JSON API**, and several major
  explorers rate-limit or serve anti-bot challenges to unauthenticated scraping. Production
  deployments should very likely swap `_safe_render`'s page-scrape approach for each explorer's
  public JSON API (most require a free API key, which would need to be supplied by whichever
  off-chain process triggers `request_corroboration`, not stored on-chain) or a public RPC
  provider's `eth_getTransactionReceipt`. The consensus architecture -- fixed sources, per-source
  classification, code-level asymmetric aggregation -- does not change either way.
- **Only two explorer operators are configured per chain.** This is enough to have *some*
  redundancy against a single provider's outage or bug, but it is not resistant to a scenario where
  both configured operators happen to share upstream infrastructure or a common indexing bug.
  Adding a third independent source per chain would only raise CONFIRMED's bar further, per the
  section above.
- **No sybil resistance on who may submit a claim.** Anyone can submit a claim about any
  transaction on any supported chain; nothing stops someone from submitting claims about
  transactions unrelated to any real use case. This is intentional -- the contract's job is only to
  answer "does the evidence support this specific claimed parameter set," never to judge whether
  the claim was worth asking in the first place -- but it does mean `list_claims` will accumulate
  claims a downstream consumer never asked about, which callers should filter for their own
  `claim_id`s rather than trusting the full list as curated.
