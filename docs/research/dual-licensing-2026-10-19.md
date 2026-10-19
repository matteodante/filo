# Filo dual-licensing decision

Research date: 2026-10-19. Scope: original Filo code and project content, not third-party
materials. This is an implementation recommendation from primary sources, not a legal opinion
or a guarantee of enforceability in any jurisdiction.

## Recommendation

Keep the public release under `AGPL-3.0-or-later` and its unmodified license text. Offer a
separately negotiated commercial agreement from Matteo Dante for uses requiring permissions
beyond the AGPL, subject to verifying rights in the exact deliverable. Commercial use that
complies with the AGPL remains allowed without paying Matteo. This matches the owner's stated
choice and does not promise exclusivity against compliant competitors. A copyright owner may
offer the same code under different nonexclusive licenses; existing public rights cannot be
withdrawn. [GNU FAQ: alternative licenses and existing grants](https://www.gnu.org/licenses/gpl-faq.en.html#ReleaseUnderGPLAndNF).

Publish a commercial-licensing policy and contact route, not an unsigned blanket proprietary
license. Keep package metadata describing the actual public license. Do not describe all
commercial use as requiring purchase, or add a noncommercial restriction to the AGPL.

## AGPL boundaries

- Commercial distribution and paid support are allowed (§4). Fees cannot restrict rights
  already granted by the license (§10).
- Conveying source or binaries triggers the applicable notice, licensing and Corresponding
  Source obligations (§§4–6).
- A modified version supporting remote network interaction must prominently offer its
  Corresponding Source, without charge, to all remote users (§13). This includes internal
  remote users; a private company network is not an automatic exception.
- Purely local private use does not require public release (§2). Section 13 is not a blanket
  requirement to publish every unmodified hosted program or unrelated service.
- Required source goes to the relevant recipients/users; the license does not generally
  require publication to the entire world. Recipients retain redistribution rights.
- Outputs and unrelated aggregates are not automatically covered (§§2, 5). Combined-work
  boundaries require facts, not a claim that every API consumer becomes AGPL.
- Compliant existing grants are irrevocable (§2); commercial terms cannot revoke previous
  public releases.

Source: [GNU AGPL v3, especially §§2, 4–6, 10, 13](https://www.gnu.org/licenses/agpl-3.0.html).
The official plaintext was retrieved successfully during this research after HTML fetches
timed out: [official plaintext](https://www.gnu.org/licenses/agpl-3.0.txt).

## Contributor policy

The existing DCO certifies authority to submit under the indicated open-source license. It does
not itself grant Matteo the additional proprietary relicensing rights needed for this model.
Do not reinterpret old sign-offs as CLA acceptance.
[Developer Certificate of Origin 1.1](https://developercertificate.org/).

Use a short, versioned contributor license agreement with the following deliberate limits:

1. Name Matteo Dante as the recipient and identify Filo and the submitted PR contributions.
2. Contributors keep copyright and may reuse/license their own contributions elsewhere.
3. Grant a nonexclusive, worldwide, royalty-free, continuing copyright license, explicitly
   permitting modification, distribution and sublicensing under commercial terms for Filo and
   products incorporating or derived from it. Include the contribution-related patent rights
   necessary for those permissions; do not demand unrelated patent rights.
4. Require authority from the actual rights holder, including the employer where relevant.
   Identify and exclude third-party content from any grant the contributor cannot make.
5. Bind the commercial permission to continued `AGPL-3.0-or-later` availability of each accepted
   contribution, no later than its commercial distribution. Do not promise publication of
   every separate proprietary customer's subsequent changes.
6. Avoid copyright assignment, indemnification and a blanket moral-rights waiver. Do not
   automatically apply revised CLA versions to earlier acceptances.

These are Filo policy recommendations, not a verbatim standard agreement. Harmony's copyright
license model retains ownership and its outbound option five pairs broader licensing with
continued availability under the original license. Its guide also distinguishes contributions
owned by the contributor from mixed submissions and discusses explicit agreement mechanisms.
[Harmony contributor agreement guide](https://www.harmonyagreements.org/guide.html).

Qt provides a working precedent for a copyright-retaining CLA supporting commercial licensing.
It requires explicit acceptance before contributions and distinguishes individual from corporate
authorization. Its particular terms need not be copied into Filo.
[Qt contribution agreement and acceptance process](https://www.qt.io/community/legal-contribution-agreement-qt).

For a small project, explicit acceptance in each PR is a workable minimal intake process:
record the exact CLA version, contributor identity, PR scope and acceptance statement; have the
maintainer verify every author/rightsholder before merging. Keep the evidence in the PR record.
An unsigned template checkbox or someone else's acceptance cannot stand in for the actual
rightsholder. Obtain separate, explicit consent identifying any earlier contributions before
including them in a proprietary grant. Git author names help inventory provenance but do not
prove legal title. A DCO may remain a provenance practice, but cannot replace the additional grant.

## Third-party blocker: WHO-5

At inspection, `src/filo/wellbeing.py` reproduces WHO-5 wording, and
`THIRD_PARTY_NOTICES.md` identifies separate English and Italian sources. The official
[WHO-5 publication page](https://www.who.int/publications/m/item/WHO-UCN-MSD-MHE-2024.01)
identifies its noncommercial Creative Commons license. WHO's policy requires permission for
commercial uses and says material credited to other owners may need their permission.
[WHO copyright and permissions policy](https://www.who.int/about/policies/publishing/copyright).

Therefore Matteo's Filo commercial agreement cannot authorize WHO-5 commercial use. Before a
commercial deliverable, obtain the relevant rights or remove the protected text from that
deliverable, including bundled files and copied test fixtures. Merely disabling a command or
adding an exclusion notice does not grant redistribution rights. Review the Italian translation's
separate ownership as well. Retain the current notices and do not imply the entire bundled
distribution is commercially cleared because the original software is AGPL.

Dependencies and downloaded models likewise retain their own terms. The exact commercial
artifact needs a rights/notice review; a CLA covers only rights contributors actually grant.

## Remaining decisions before signing a commercial agreement

- Establish rights in the exact version, including historical external contributions.
- Resolve or exclude WHO-5 and any other third-party restrictions in the deliverable.
- Define the actual commercial scope, price, parties and support terms in a separate agreement.
- Check the final CLA and commercial contract for the applicable jurisdiction; the references
  above establish models and license mechanics, not jurisdiction-specific legal guarantees.
