# ANSX Patent — Pre-Filing Engineering Tasks

> **DO NOT rewrite claims until all four tasks below are completed.**
> Three independent reviewers agree: the claim architecture is decided.
> Only the engineering foundation underneath needs to be resolved.

---

## Task 1: Hardware Bill of Materials (BOM)

Go to your physical prototype and fill in every field from the actual hardware.

```
NFC Chip
────────────────────────────────────────────────
Exact part number:          ___________
Manufacturer:               ___________
NFC Forum Type:             ___________ (Type 2 or Type 4)
ISO Standard:               ___________ (e.g., ISO/IEC 14443-A)
UID length (bytes):         ___________
Originality signature:      ___________ (ECDSA / ECC / None)
Signature verification key: ___________ (public key source)
EEPROM total pages:         ___________
Write-lock mechanism:       ___________ (OTP bits / lock bytes / other)
Write-lock irreversible:    ___________ (Yes / No)

Host Platform
────────────────────────────────────────────────
TPM version:                ___________ (2.0 / 1.2 / None)
TPM manufacturer:           ___________
Endorsement Key type:       ___________ (RSA-2048 / ECC-P256 / other)
Platform identity source:   ___________ (EK cert / machine UUID / other)
```

### Why this matters:
- If your chip is NTAG 424 DNA → it is Type 4, not Type 2
- If your chip is NTAG 223/224 DNA → it is Type 2
- Getting the ISO standard wrong in the claim destroys examiner credibility
- The patent cell attorney WILL ask "what hardware are you actually using?"

---

## Task 2: Co-Presence Protocol

Write the exact engineering protocol for how the NFC card and TPM
prove they were physically present together during provisioning.

```
PROVISIONING PROTOCOL
────────────────────────────────────────────────

Step 1: NFC Card generates fresh nonce
   Who generates:      ___________
   Nonce length:       ___________ bits
   How generated:      ___________ (on-card RNG / host RNG / other)

Step 2: Nonce is delivered to TPM
   Transport mechanism: ___________
   How tamper is prevented: ___________

Step 3: TPM generates attestation quote
   TPM command used:    ___________ (TPM2_Quote / other)
   PCR registers included: ___________
   Qualifying data:     ___________ (nonce ∥ UID ∥ NXP_SIG)
   Signing key:         ___________ (Attestation Key / EK / other)

Step 4: Quote verification
   Who verifies:        ___________ (NFC card / host software / both)
   What is checked:     ___________
   How replay is prevented: ___________

Step 5: C₂ derivation
   C₂ = HMAC-SHA256( ___________ , ___________ )
   
   Inputs to HMAC:     ___________
   Why C₂ cannot be produced without both devices: ___________
```

### Why this matters:
- Without a freshness mechanism, "co-presence" is a desired property,
  not a demonstrated one
- An examiner will reject the co-presence claim as insufficiently
  enabled without this protocol
- A replayed UID fed into a TPM quote does NOT prove co-presence

---

## Task 3: Deterministic Mappings

Define the exact mathematical functions. Every variable, every range.

```
FIELD SELECTION (from C₁)
────────────────────────────────────────────────
K_F = HMAC-SHA256(C₁, "FIELD")

Primitive polynomial selection:
   p(x) = PRIMITIVES_16[ K_F[0] mod 16 ]

   The 16 primitive polynomials of degree 8 over GF(2):
   [0]  x⁸ + x⁴ + x³ + x² + 1           (0x11D)
   [1]  x⁸ + x⁵ + x³ + x + 1             (0x12B)
   [2]  x⁸ + x⁵ + x³ + x² + 1            (0x12D)
   [3]  x⁸ + x⁶ + x³ + x² + 1            (0x14D)
   [4]  x⁸ + x⁶ + x⁴ + x³ + x² + x + 1  (0x15F)
   [5]  x⁸ + x⁶ + x⁵ + x + 1             (0x163)
   [6]  x⁸ + x⁶ + x⁵ + x² + 1            (0x165)
   [7]  x⁸ + x⁶ + x⁵ + x³ + 1            (0x169)
   [8]  x⁸ + x⁶ + x⁵ + x⁴ + 1            (0x171)
   [9]  x⁸ + x⁷ + x² + x + 1             (0x187)
   [10] x⁸ + x⁷ + x³ + x² + 1            (0x18D)
   [11] x⁸ + x⁷ + x⁵ + x³ + 1            (0x1A9)
   [12] x⁸ + x⁷ + x⁶ + x + 1             (0x1C3)
   [13] x⁸ + x⁷ + x⁶ + x³ + x² + x + 1  (0x1CF)
   [14] x⁸ + x⁷ + x⁶ + x⁵ + x² + x + 1  (0x1E7)
   [15] x⁸ + x⁷ + x⁶ + x⁵ + x⁴ + x² + 1 (0x1F5)

   NOTE: Verify this list independently. These are candidate
   primitive polynomials of degree 8 over GF(2). Cross-check
   against a verified mathematical reference before filing.


CODE RATE SELECTION (from C₁, domain-separated)
────────────────────────────────────────────────
K_R = HMAC-SHA256(C₁, "RATE")

   N_min = ___________  (minimum shards, e.g., 6)
   N_max = ___________  (maximum shards, e.g., 24)
   N = (K_R[0] mod (N_max - N_min + 1)) + N_min


EVALUATION COORDINATE SELECTION (from C₂)
────────────────────────────────────────────────
K_G = HMAC-SHA256(C₂, "GEOMETRY")

   Generate N distinct nonzero elements of GF(2⁸)/p(x):
   
   Method: ___________
   (e.g., use successive bytes of K_G to index into GF(2⁸)\{0},
    skip duplicates, extend K_G via HMAC chain if needed)

   Edge case — what if K_G produces duplicate coordinates:
   ___________
```

### Why this matters:
- Domain separation (separate HMAC calls with different labels) is
  cryptographically correct; byte-slicing a single HMAC is not
- "Statistical independence" of outputs is a modeling assumption,
  not a conclusion from domain separation — state this explicitly
- The 16-polynomial list must be verified against a mathematical
  reference before it goes into any filing

---

## Task 4: Prior Art Search

Search these specific intersections. Record what you find.

```
Search A: Hardware-derived GF primitive polynomial selection
   Found:    [ ] Yes  [ ] No
   References: ___________

Search B: Split hardware roots controlling distinct codec parameters
   Found:    [ ] Yes  [ ] No
   References: ___________

Search C: NFC ECC originality signatures in cryptographic protocols
   Found:    [ ] Yes  [ ] No
   References: ___________

Search D: TPM + NFC physical co-presence binding protocols
   Found:    [ ] Yes  [ ] No
   References: ___________

Search E: Hardware-derived dynamic Reed-Solomon code rates
   Found:    [ ] Yes  [ ] No
   References: ___________

Search F: Intersection of A + B + C + D + E together
   Found:    [ ] Yes  [ ] No
   References: ___________

Key prior art to specifically check against:
   [ ] Suh & Devadas 2007 — PUF + ECC
   [ ] McEliece 1978 — Secret code structure
   [ ] Any NXP NFC authentication patents
   [ ] Any TPM-based attestation + erasure coding patents
```

### Why this matters:
- The patent cell will ask "have you searched prior art?"
- If Search F returns nothing, your combination is novel
- If any of A-E returns a hit, you need to differentiate

---

## Frozen Claim Architecture (Agreed by All Reviewers)

DO NOT change this structure. Fill in the engineering details above,
then write the claims to match.

```
INDEPENDENT CLAIM
├── C₁ → field structure (primitive polynomial)
├── C₂ → evaluation geometry (x-coordinates)
├── Neither alone reconstructs
└── Algebraic verification (residual indication)

DEPENDENT CLAIMS
├── Dep 1: NFC manufacturer originality signature (ECDSA)
├── Dep 2: Write-locked EEPROM immutability
├── Dep 3: UID-bound polynomial permutation
├── Dep 4: TPM/NFC co-presence protocol (with freshness)
├── Dep 5: C₁-derived dynamic N (domain-separated)
├── Dep 6: Domain-separated HMAC derivation functions
└── Dep 7: Conditional probability bound (under stated assumptions)
```

---

## Corrections Already Locked In

These are agreed by all three reviewers. Non-negotiable.

| Item | Wrong | Correct |
|---|---|---|
| Primitive polynomial count | 30 | **16** |
| Collision probability | 2⁻⁵⁷ | **2⁻⁷⁰·⁷² (conditional)** |
| NFC standard | "ISO 14443-3 Type 2" always | **Verify from actual chip** |
| "Non-enumerable" | Used in Claim 3 | **Replace: "card-specific binding"** |
| "Mathematical proof" | Used in Claim 1 | **Replace: "algebraic indication"** |
| "Statistically independent" | Stated as conclusion | **State as modeling assumption** |
| C₁ partitioning | Byte slicing | **Domain-separated HMACs** |
| Co-presence | Asserted | **Must define freshness protocol** |

---

## What To Do With This Document

1. Print this document
2. Fill in every blank field from your actual hardware and engineering
3. Bring the completed document + the claims file to the patent cell
4. The patent cell attorney drafts final claims from YOUR engineering facts

You are the inventor. They are the drafter. Give them facts, not drafts.
