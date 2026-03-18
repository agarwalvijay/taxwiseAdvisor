# TaxWise Advisor — Tax Logic Reference

> **Status:** Requires CPA review before Phase 4 (reasoning engine build) begins.
> All tax rules encoded in the reasoning engine prompts must trace back to this document.
> When tax law changes (annual bracket adjustments, IRMAA thresholds, etc.),
> update this file first, then update the affected prompts.

---

## 1. Federal Income Tax Brackets (2026)

### Married Filing Jointly (MFJ)

| Taxable Income | Marginal Rate |
|---|---|
| $0 – $23,850 | 10% |
| $23,851 – $96,950 | 12% |
| $96,951 – $206,700 | 22% |
| $206,701 – $394,600 | 24% |
| $394,601 – $501,050 | 32% |
| $501,051 – $751,600 | 35% |
| $751,601+ | 37% |

### Single / Married Filing Separately (MFS)

| Taxable Income | Marginal Rate |
|---|---|
| $0 – $11,925 | 10% |
| $11,926 – $48,475 | 12% |
| $48,476 – $103,350 | 22% |
| $103,351 – $197,300 | 24% |
| $197,301 – $250,525 | 32% |
| $250,526 – $626,350 | 35% |
| $626,351+ | 37% |

### Head of Household (HOH)

| Taxable Income | Marginal Rate |
|---|---|
| $0 – $17,000 | 10% |
| $17,001 – $64,850 | 12% |
| $64,851 – $103,350 | 22% |
| $103,351 – $197,300 | 24% |
| $197,301 – $250,500 | 32% |
| $250,501 – $626,350 | 35% |
| $626,351+ | 37% |

### Standard Deduction (2026)

| Filing Status | Amount |
|---|---|
| MFJ | $30,000 |
| Single | $15,000 |
| HOH | $22,500 |
| MFS | $15,000 |
| Additional (age 65+, per person) | $1,600 (MFJ) / $2,000 (Single/HOH) |

> **Note:** A new senior deduction of $6,000 applies for tax years 2025–2028 for
> filers age 65+. Phases out above $75,000 MAGI (single) / $150,000 MAGI (MFJ).
> This is in addition to the standard deduction.

---

## 2. Long-Term Capital Gains (LTCG) Tax Rates (2026)

| Filing Status | 0% Rate | 15% Rate | 20% Rate |
|---|---|---|---|
| MFJ | $0 – $96,700 | $96,701 – $600,050 | $600,051+ |
| Single | $0 – $48,350 | $48,351 – $533,400 | $533,401+ |
| HOH | $0 – $64,750 | $64,751 – $566,700 | $566,701+ |

**Key rule:** LTCG rates apply to assets held longer than 12 months. Short-term
capital gains are taxed as ordinary income.

---

## 3. Net Investment Income Tax (NIIT)

- **Rate:** 3.8% on net investment income
- **Threshold:** $200,000 MAGI (single) / $250,000 MAGI (MFJ)
- **Applies to:** dividends, interest, capital gains, rental income, passive income
- **Does NOT apply to:** wages, self-employment income, active business income,
  distributions from retirement accounts (401k, IRA, Roth)
- **Roth conversion impact:** Roth conversions increase MAGI, which can push
  investment income over the NIIT threshold even if the conversion itself is not
  subject to NIIT

**Reasoning engine rule:** Flag NIIT exposure when client MAGI (including conversion
amount) exceeds the applicable threshold. Calculate NIIT on net investment income
only — not on the conversion amount itself.

---

## 4. Roth Conversion Rules

### What Can Be Converted

- Traditional IRA → Roth IRA ✓
- 401(k) (pre-tax) → Roth IRA ✓ (after separation from employer, or if plan allows in-plan conversion)
- 401(k) (pre-tax) → Roth 401(k) ✓ (in-plan conversion, if plan allows)
- SEP IRA → Roth IRA ✓
- SIMPLE IRA → Roth IRA ✓ (after 2-year holding period)

### Tax Treatment

- Converted amount is added to ordinary income in the year of conversion
- No 10% early withdrawal penalty on conversions (but does apply to distributions
  from the converted amount within 5 years if under age 59½ — the 5-year rule)
- State income tax treatment varies — see Section 9

### The 5-Year Rule (Two Separate Rules)

**Rule 1 — Roth IRA earnings:** A Roth IRA must be at least 5 years old before
earnings can be withdrawn tax-free. The clock starts January 1 of the first tax
year for which a Roth IRA contribution was made.

**Rule 2 — Conversions:** Each conversion has its own 5-year holding period for
the purposes of the 10% early withdrawal penalty (for those under 59½). After
age 59½, this rule does not apply.

### Conversion Strategy Logic for the Reasoning Engine

**Bracket fill strategy:**
1. Calculate taxable income without conversion
2. Determine "room" to top of current bracket (or next target bracket)
3. Convert up to that room — do not overshoot unintentionally
4. Check all secondary effects (IRMAA, NIIT, SS taxation, ACA) before finalizing

**Multi-year sequencing:**
- Prioritize years with lowest income (sabbaticals, early retirement gap, years
  before Social Security starts)
- The window between retirement and RMD start at age 73 is typically the primary
  conversion window
- The window between retirement and Social Security start (if delaying to 70)
  is especially valuable — no SS income yet, no W-2 income, lowest lifetime AGI

**Urgency factors that increase conversion priority:**
- Large projected pre-tax balance → large projected RMDs at 73
- RMDs projected to push into a higher bracket or trigger IRMAA
- Tax rates expected to increase (legislative risk)
- Estate planning goals (Roth has no RMDs, ideal for heirs)

---

## 5. Required Minimum Distributions (RMDs)

### RMD Start Age

- **Age 73** for anyone who turned 72 after December 31, 2022 (SECURE 2.0)
- **Age 75** for anyone who turns 74 after December 31, 2032

### Accounts Subject to RMDs

- Traditional IRAs ✓
- SEP IRAs ✓
- SIMPLE IRAs ✓
- 401(k), 403(b), 457(b) plans ✓ (exception: still-working employees at current employer
  who are not 5%+ owners can delay until retirement)
- Roth 401(k) — **NO longer subject to RMDs** (SECURE 2.0, effective 2024) ✓
- Roth IRA — **never subject to RMDs** during the owner's lifetime ✓

### RMD Calculation

```
RMD = Account Balance (Dec 31 of prior year) ÷ Life Expectancy Factor
```

Life expectancy factors from IRS Uniform Lifetime Table (2022 update):

| Age | Factor |
|---|---|
| 72 | 27.4 |
| 73 | 26.5 |
| 74 | 25.5 |
| 75 | 24.6 |
| 76 | 23.7 |
| 77 | 22.9 |
| 78 | 22.0 |
| 79 | 21.1 |
| 80 | 20.2 |
| 85 | 16.0 |
| 90 | 12.2 |
| 95 | 8.9 |

**Reasoning engine use:** Project pre-tax balance at RMD start age using a conservative
growth assumption (default: 6% nominal, configurable). Calculate projected first RMD.
Determine what bracket that RMD pushes the client into, accounting for other retirement
income (Social Security, pension, investment income).

### RMD + Social Security Interaction

RMDs count as ordinary income. Combined with Social Security income, large RMDs can:
1. Push the client into a higher income tax bracket
2. Trigger or increase Social Security benefit taxation (up to 85%)
3. Trigger IRMAA Medicare surcharges (2-year lookback)

This is the primary driver of Roth conversion urgency for clients with large pre-tax balances.

---

## 6. Social Security Benefit Taxation

### Provisional Income Formula

```
Provisional Income = AGI + Tax-Exempt Interest + 50% of SS Benefits
```

### Taxation Thresholds

| Filing Status | Provisional Income | % of SS Benefits Taxable |
|---|---|---|
| MFJ | $0 – $32,000 | 0% |
| MFJ | $32,001 – $44,000 | Up to 50% |
| MFJ | $44,001+ | Up to 85% |
| Single | $0 – $25,000 | 0% |
| Single | $25,001 – $34,000 | Up to 50% |
| Single | $34,001+ | Up to 85% |

**Maximum taxable:** 85% of SS benefits — never 100%.

**Roth conversion impact:** Roth conversions increase AGI, which increases provisional
income, which can push more SS benefits into taxation. The reasoning engine must model
this interaction when calculating effective tax rate on a conversion.

**Strategic note:** The years just before SS starts are ideal for conversions because:
- No SS income to push over the provisional income thresholds
- Once SS starts, every dollar of conversion has a higher effective marginal rate

---

## 7. Medicare IRMAA (Income-Related Monthly Adjustment Amount)

IRMAA adds surcharges to Medicare Part B and Part D premiums for high-income beneficiaries.
The determination uses MAGI from **2 years prior** (e.g., 2026 Medicare premiums are based
on 2024 MAGI).

### Part B IRMAA Surcharges (2026 — subject to annual adjustment)

| MAGI (MFJ) | MAGI (Single) | Monthly Part B Premium |
|---|---|---|
| ≤ $212,000 | ≤ $106,000 | $185.00 (base) |
| $212,001 – $266,000 | $106,001 – $133,000 | $259.00 (+$74) |
| $266,001 – $320,000 | $133,001 – $160,000 | $370.00 (+$185) |
| $320,001 – $394,000 | $160,001 – $197,000 | $480.80 (+$295.80) |
| $394,001 – $749,999 | $197,001 – $499,999 | $591.90 (+$406.90) |
| $750,000+ | $500,000+ | $628.90 (+$443.90) |

> **Note:** IRMAA thresholds are adjusted annually for inflation. Always verify
> current-year thresholds before updating reasoning engine prompts.

### IRMAA in Roth Conversion Planning

**2-year lookback:** A Roth conversion in 2026 affects 2028 Medicare premiums.
The reasoning engine must:
1. Project income 2 years forward from each conversion year
2. Check whether post-conversion MAGI crosses an IRMAA tier
3. Flag if conversion pushes into a new IRMAA tier
4. Suggest reducing conversion amount to stay below tier if the IRMAA cost exceeds benefit

**IRMAA cliff awareness:** IRMAA tiers are cliffs, not gradual. Converting $1 above a
threshold can cost thousands in additional premiums per year. The reasoning engine should
calculate the exact dollar cost of crossing each tier and include this in the recommendation.

### Part D IRMAA

Similar tier structure to Part B. Add approximately $12–$81/month depending on tier.
Include in total IRMAA cost calculation.

---

## 8. ACA (Affordable Care Act) Subsidy Considerations

Relevant for clients who:
- Are under age 65 (not yet Medicare-eligible)
- Do not have employer-sponsored health insurance
- Are purchasing insurance through the ACA marketplace

### Premium Tax Credit (PTC) Eligibility

Subsidies are available for households with MAGI between 100% and 400% of the
Federal Poverty Level (FPL). Above 400% FPL, no subsidy (the cliff was removed
through 2025 but check current law — the enhanced subsidies may have changed).

### 2026 FPL Reference (48 contiguous states)

| Household Size | 100% FPL | 400% FPL |
|---|---|---|
| 1 | ~$15,650 | ~$62,600 |
| 2 | ~$21,150 | ~$84,600 |
| 3 | ~$26,650 | ~$106,600 |
| 4 | ~$32,150 | ~$128,600 |

> **Verify current FPL amounts annually** — adjusted each year.

### Roth Conversion Impact

Roth conversions count as MAGI for ACA purposes. A conversion that pushes MAGI
above the PTC threshold can eliminate thousands in annual subsidies.

**Reasoning engine rule:** If client is pre-Medicare and ACA-relevant
(`aca_relevant: true` in snapshot), calculate the subsidy impact of each
conversion amount. Flag conversions that approach or cross PTC thresholds.
This is a hard constraint in ACA-relevant years — exceeding the threshold
is often a larger cost than any conversion benefit.

---

## 9. Tax-Loss Harvesting (TLH) Rules

### Eligibility

- **Only taxable brokerage accounts** — not traditional IRA, 401k, or Roth accounts
- Losses in retirement accounts are never deductible

### Wash-Sale Rule (IRC Section 1091)

A capital loss is disallowed if the taxpayer:
1. Sells a security at a loss, AND
2. Purchases the same or a "substantially identical" security within 30 days
   **before or after** the sale, in **any** account (including IRAs, Roth IRAs,
   spouse's accounts)

**Substantially identical:** Generally means the exact same security. Similar but
not identical ETFs tracking different indexes are generally NOT substantially identical.
Example: Selling VTI (Total US Market) and buying ITOT (also Total US Market) is
likely a wash sale. Selling VTI and buying VUG (Growth) is generally not.

**Reasoning engine rule:** When recommending a TLH sale, always suggest a replacement
security and note the wash-sale risk level. Flag if the client holds the same security
in an IRA.

### Capital Loss Application Order

1. Short-term losses offset short-term gains first
2. Long-term losses offset long-term gains first
3. Net short-term losses can offset long-term gains
4. Net long-term losses can offset short-term gains
5. Net losses offset ordinary income up to **$3,000 per year**
6. Remaining net losses **carry forward** indefinitely

### Holding Period

- **Short-term:** Held 12 months or less → taxed as ordinary income
- **Long-term:** Held more than 12 months → taxed at LTCG rates (0%, 15%, 20%)

### NIIT Interaction

For clients subject to NIIT (MAGI > $200k/$250k), harvesting losses also reduces
NIIT exposure. The 3.8% NIIT applies to net investment income, so losses offset gains
dollar-for-dollar for NIIT purposes in addition to income tax purposes.

**Effective marginal benefit of TLH for NIIT-exposed clients:**
- Long-term gain rate: 15% or 20% LTCG + 3.8% NIIT = 18.8% or 23.8%
- Each dollar of harvested loss saves up to 23.8 cents in taxes

---

## 10. Asset Location Principles

Asset location is the strategy of holding assets in the account type where they
are taxed most favorably.

### Framework

| Asset Type | Best Location | Reason |
|---|---|---|
| High-yield bonds, bond funds | Traditional IRA / 401k | Interest taxed as ordinary income — shield it |
| REITs | Traditional IRA / 401k | REIT dividends taxed as ordinary income |
| Actively managed funds (high turnover) | Traditional IRA / 401k | Frequent capital gain distributions |
| High-growth equities | Roth IRA / Roth 401k | Tax-free compounding most valuable on highest-growth assets |
| Dividend-paying stocks | Roth IRA | Dividends permanently tax-free |
| Index ETFs (low turnover) | Taxable brokerage | Low dividends, qualified dividends, long-term LTCG rates |
| Municipal bonds | Taxable brokerage | Already tax-exempt — no benefit from tax-advantaged wrapper |
| I-Bonds / TIPS | Taxable brokerage or IRA | Inflation protection; IRA defers inflation-adjustment taxation |
| Cash / money market | Taxable or IRA | Low return — opportunity cost of Roth space is high |

### Asset Location in TLH Context

After a TLH sale, the replacement security may have different tax characteristics
than the original. Consider whether the replacement belongs in the same account type.

---

## 11. State Tax Considerations

### Illinois (IL) — Default Client State

- **Flat income tax rate:** 4.95%
- **Retirement income exemption:** Illinois does NOT tax retirement income including:
  - Social Security benefits
  - Pension income (from qualified plans)
  - IRA distributions (traditional and Roth)
  - 401(k) distributions
- **Roth conversion treatment:** Roth conversions ARE taxable in Illinois as ordinary
  income in the year of conversion (they are not retirement distributions — they are
  income events)
- **Capital gains:** Taxed as ordinary income at 4.95% flat rate
- **LTCG rate for IL clients:** Federal LTCG rate + 4.95% state = total effective rate

> **Important for reasoning engine:** When calculating net conversion benefit for
> IL clients, include 4.95% state tax on conversion amounts (since conversions are
> ordinary income), but recognize that future Roth withdrawals will be IL state-tax-free.
> This increases the attractiveness of conversions for IL clients compared to
> states that also tax retirement distributions.

### States With No Income Tax

No state income tax: AK, FL, NV, NH (interest/dividends only), SD, TN (interest/dividends only), TX, WA, WY

For clients in these states, state tax has no effect on Roth conversion math.
The federal analysis is the complete picture.

### States That Do NOT Tax Retirement Income

Many states exempt pension and retirement income. This reduces the cost of taking
IRA/401k distributions in retirement vs. converting to Roth and paying state tax now.
The reasoning engine must flag when state tax treatment significantly changes the
conversion recommendation. Advisor should verify current state law.

---

## 12. Effective Marginal Rate Calculation

The effective marginal rate on a Roth conversion is NOT simply the marginal
federal bracket. It includes all of the following for each dollar converted:

```
Effective Marginal Rate =
  Federal income tax bracket rate
  + State income tax rate (on conversion)
  + IRMAA cost per dollar of income (if crossing a tier) / income above threshold
  + Additional SS benefit taxation (0.85 × marginal rate × SS inclusion ratio change)
  + NIIT on any investment income pushed over threshold
  - Future tax savings from avoided RMDs (present value)
  - Future IRMAA savings from reduced RMDs (present value)
```

This is why a single-year snapshot is insufficient — optimal conversion planning
requires modeling the full lifetime tax picture.

**Reasoning engine approach:** Model each conversion year's effective marginal rate
using the snapshot data. Flag years where the effective marginal rate exceeds the
expected marginal rate in retirement (no conversion benefit) vs. years where it is
materially lower (conversion is attractive).

---

## 13. Key Numbers Reference (2026)

| Item | Amount |
|---|---|
| 401(k) contribution limit | $23,500 |
| 401(k) catch-up (age 50+) | $7,500 additional |
| 401(k) catch-up (age 60-63, SECURE 2.0) | $11,250 additional |
| IRA contribution limit | $7,000 |
| IRA catch-up (age 50+) | $1,000 additional |
| Roth IRA income phase-out (MFJ) | $236,000 – $246,000 |
| Roth IRA income phase-out (Single) | $150,000 – $165,000 |
| HSA contribution limit (self-only) | $4,300 |
| HSA contribution limit (family) | $8,550 |
| HSA catch-up (age 55+) | $1,000 additional |
| Annual gift tax exclusion | $18,000 per recipient |
| Estate tax exemption | $13,990,000 (per person) |
| RMD start age | 73 |
| Medicare eligibility age | 65 |
| Social Security full retirement age (born 1960+) | 67 |
| SS early filing (reduced benefit) | Age 62 |
| SS delayed filing (maximum benefit) | Age 70 |
| SS delayed filing bonus | 8% per year beyond FRA |
| Capital loss ordinary income offset limit | $3,000/year |

> **Annual update required:** IRS adjusts brackets, contribution limits, IRMAA thresholds,
> and other figures annually. Review and update this document each January.

---

## 14. Disclaimer Language (Required on All Reports)

The following disclaimer must appear on every client-facing report generated by
TaxWise Advisor:

> *This analysis was prepared by [Advisor Name] using TaxWise Advisor software as a
> planning tool. It is intended to support informed discussion between you and your
> financial advisor and does not constitute independent financial, tax, or legal advice.
> Tax laws change frequently and individual circumstances vary. The projections and
> recommendations in this report are based on information provided as of [Report Date]
> and involve assumptions about future income, tax rates, and investment returns that
> may not materialize. Before implementing any strategy described in this report, please
> consult with a qualified tax professional. Your advisor is responsible for the
> recommendations made to you.*

---

## 15. CPA Review Checklist

Before the reasoning engine goes into production (Phase 4 completion), a CPA should
verify the following in this document and in the corresponding reasoning prompts:

- [ ] Federal tax brackets are current for the applicable tax year
- [ ] Standard deduction amounts are correct
- [ ] LTCG rate thresholds are correct
- [ ] IRMAA tier thresholds and premium amounts are current
- [ ] RMD life expectancy table factors are from the correct IRS table (post-2022 update)
- [ ] RMD start age is correctly applied (73 vs. 75 depending on birth year)
- [ ] Social Security taxation thresholds and formula are correct
- [ ] NIIT thresholds and mechanics are correctly described
- [ ] Wash-sale rule description (30-day window, all accounts) is complete
- [ ] Capital loss ordering rules are correctly described
- [ ] Illinois state tax treatment of conversions vs. distributions is accurate
- [ ] ACA/PTC MAGI thresholds are current
- [ ] The 5-year Roth rules (two separate rules) are correctly distinguished
- [ ] Disclaimer language has been reviewed by legal counsel

**CPA sign-off:**
- Name: ___________________
- Date: ___________________
- Notes: ___________________
