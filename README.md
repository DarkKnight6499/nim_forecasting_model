# Net Interest Margin & ALM Forecasting Model

A monthly, dynamic asset/liability (ALM) simulation that projects a bank's
Net Interest Margin under multiple interest-rate scenarios, plus the standard
bank treasury/ALCO risk suite built on the same balance sheet: interest rate
sensitivity gap, duration gap & EVE sensitivity, a structural liquidity
statement, and earnings-at-risk.

## How it works

1. **Balance sheet positions** ([balance_sheet.yaml](balance_sheet.yaml), loaded via
   [core/balance_sheet.py](core/balance_sheet.py) into [core/position.py](core/position.py)
   `Position` objects) - each asset/liability line (loans, securities, deposits, borrowings)
   has a starting balance, rate, and a repricing behavior:
   - `variable` - reprices off a rate index (see below), on a fixed cadence
     (`reset_frequency_months`; default 1 = every month). A staggered book (e.g. C&I
     loans on a 3-month reset) is split into that many cohorts by the engine, each due
     to reset in a different month of the cycle, so roughly a third reprices every
     month instead of the whole book jumping together every third month.
   - `administered` - bank-controlled rate, partial/lagged repricing (e.g. savings, MMDA).
     NOW and Savings/MMDA are each split into **core** (stable, seasonally-patterned, sticky -
     long `behavioral_duration_years`, slow `liquidity_decay_annual`) and **non-core**
     (volatile/rate-shopped - short duration, fast decay) sub-positions - standard
     non-maturity-deposit (NMD) behavioral modeling. These parameters are assumptions by
     default, but [core/nmd_estimation.py](core/nmd_estimation.py) can estimate them from an
     actual balance history instead (see `--deposit-history` below). `seasonal: true` applies
     `config.SEASONALITY_INDEX_DEPOSITS` to that position's growth; `pricing_elasticity` (see
     [core/elasticity.py](core/elasticity.py)) additionally slows or reverses growth when this
     position's own rate lags the market (e.g. a low-beta book in a rising-rate cycle). Each
     position exposes a `repricing_schedule()` (drives the rate sensitivity gap) and a
     `cashflow_schedule()` (drives the structural liquidity statement) - deliberately
     different for non-maturity deposits, since a rate reset is not a cash outflow.
   - `fixed_amortizing` - held as vintage cohorts. Each existing cohort's rate is locked
     for life (a real fixed-rate loan doesn't reprice mid-life); it runs off at a CPR
     that speeds up the further its own coupon sits above the current new-production
     rate (`refi_sensitivity`, capped at `cpr_max`) - the mortgage "burnout" effect.
     Runoff plus growth becomes a new cohort priced at the current curve tenor + spread
     (e.g. CRE, mortgage, consumer loans).
   - `laddered` - 1/N of the balance matures each month; only `renewal_rate` of that
     maturing slice actually rolls into new production (default 1.0) - the rest is a real
     funding outflow the plug absorbs, not automatic rollover. `early_withdrawal_annual`
     adds extra runoff on top of scheduled maturities. New/renewed production prices at
     the position's own curve tenor + spread (e.g. securities, CDs, term debt).

   Rate indices ([core/indices.py](core/indices.py)): `SHORT` (curve spot at ~1 month),
   `TENOR`/`FIXED` (curve spot at the position's own `origination_tenor_years`), `ADMIN`
   (the bank-controlled lag/beta mechanic above), and `MCLR` - a backward-looking
   marginal cost of funds computed *from the model's own liability side* each month
   (weighted new deposit production cost + the current short-term borrowing rate, see
   `core/indices.py::compute_mclr`), not an external benchmark. C&I loans are MCLR-linked
   by default: the real liability-cost -> MCLR -> asset-pricing -> NIM chain.
   SHORT/TENOR/FIXED (and TBILL3M, unused by the default book) each carry a per-index
   basis overlay (`config.INDEX_BASIS`, `curve/basis.py`) added on top of the base curve
   when projecting that index - so one index can move differently from the base curve, or
   from another index, instead of every index reading the same curve point directly.
   Discounting (EVE, MTM, FTP) always stays on the base curve plus the position's own
   z-spread; only index projection reads the basis overlay.
2. **Yield curve & rate scenarios** ([curve/](curve/)) - a full term structure
   (`curve/yield_curve.py`), not a single benchmark scalar: spot/forward rates,
   discount factors, and curve-shape shocks (`curve/shocks.py`: parallel, steepener,
   flattener, twist). The base curve comes from the live US Treasury daily par yield
   curve (`data_sources/treasury_curve.py`), falling back to an illustrative shape
   anchored at `config.STARTING_BENCHMARK_RATE` (optionally FRED-anchored) if the
   fetch fails. Scenarios (base/flat, ±100bps, ±200bps by default) ramp in linearly
   over 12 months (configurable) as curve transformations (`curve/scenarios.py`). A
   scenario can also be a `(shift_fn, basis_shocks)` pair instead of a plain shift
   function: `basis_shocks` is `{index_name: bps_shift}`, ramped the same way but
   applied to that index's overlay instead of the base curve - two shipped scenarios,
   `"Funding basis widening"` (SHORT +25bps) and `"Term-priced spread compression"`
   (TENOR/FIXED -50bps), demonstrate this without moving the discount curve at all.
   Only the NII engine (new/reset production pricing) consumes index basis. Full-
   revaluation EVE runs its own fixed six-scenario IRRBB set (curve shocks only, see
   below) and never sees these two scenarios at all; the linear duration approximation
   does iterate `config.RATE_SCENARIOS` but only reads curve shift magnitude, so
   "Funding basis widening" and "Term-priced spread compression" show 0.00 delta EVE
   there by design, while still visibly moving NII/NIM.
3. **Dynamic engine** ([core/engine.py](core/engine.py)) - steps every position's cohorts
   forward month by month with growth, computes interest income/expense, and annualized
   NIM = (interest income − interest expense) × 12 / avg earning assets. Enforces the
   balance sheet identity (assets = liabilities + equity) every month from month 1
   onward: one designated position (short-term borrowings) absorbs a funding
   shortfall, another (fed funds sold) absorbs a surplus, and equity grows each
   month by retained net interest income.
4. **Static ALM reports** ([model/alm_reports.py](model/alm_reports.py)) - point-in-time snapshots
   of the *current* balance sheet (no growth), the classic complement to the dynamic engine above:
   - **Rate Sensitivity Gap**: RSA/RSL and cumulative gap across 7 repricing time bands
   - **Duration Gap & EVE Sensitivity**: effective duration per position, DGAP = D_A − (L/A)·D_L,
     and ΔEVE (economic value of equity) under each instantaneous rate shock, as % of capital
   - **Structural Liquidity Statement**: inflow/outflow cashflow gap by time band, using
     core-deposit decay (not repricing lag) for non-maturity deposits
   - **Earnings-at-Risk**: cumulative NII impact vs. base at 3/6/12/24-month horizons, plus the
     full monthly delta series - a bank can be asset-sensitive near-term and liability-sensitive
     further out, so a single-horizon number can hide a real crossover (see the PNC example below)
5. **FTP / ALM desk P&L** ([core/ftp/](core/ftp/)) - Funds Transfer Pricing, with the method
   selectable per position (`ftp_method` in `balance_sheet.yaml`, dispatched via
   `core/ftp/registry.py`):
   - **matched_maturity** - origination-locked: a `fixed_amortizing` cohort's transfer rate is set
     once, from the curve at its origination month and its own tenor, and never moves again (this
     is what immunizes a fixed-rate loan's customer margin from the scenario's rate path); a
     `variable` position's transfer rate is set at its reset tenor and re-fixed only on reset
     months. This is the default for both.
   - **pooled_replicating** - a rolling ladder of tranches (behavioral-duration-equivalent for
     administered/NMD positions, `ladder_months` for laddered positions), re-fixing only the
     maturing 1/N slice each month. Default for `administered` and `laddered` positions, which
     have no single real origination point.
   - **straight_spread** - flat spread over the overnight rate, no tenor lookup; a simple opt-in
     fallback.

   Every position is charged/credited its FTP rate, splitting total NII into **customer margin**
   (what business units earn vs. the internal transfer price) and **ALM/Treasury desk P&L** (the
   transfer-pricing net - this *is* "ALM NII"). The two always reconcile exactly to total NII by
   construction (checked every run). Also reports ALM desk P&L stability across rate scenarios -
   with origination-locked pricing this stays far flatter across scenarios than a floating
   transfer rate would (see below).
6. **Liquidity Coverage Ratio** ([core/lcr.py](core/lcr.py)) - LCR = HQLA / max(net 30-day outflows,
   25% of gross outflows), Basel III (BCBS238) standard factors. The investment book is split by HQLA
   level (`hqla_level` in balance_sheet.yaml: `Cash & central bank reserves`/`Treasuries` are L1,
   `Agency MBS` is L2A, `Municipal & corporate bonds` is L2B), each haircut and the L2B/L2 composition
   caps applied via the standard closed-form formulas (no iteration needed). Liabilities get
   `lcr_outflow_category` (stable/less-stable retail, term deposit, wholesale) - administered (NMD) and
   variable liabilities run off at their factor on the full balance; laddered liabilities only on the
   current month's maturing slice, since only cash actually due within 30 days counts. Tracked monthly
   across every scenario (`--ftp-recalibrate`-style before/after isn't needed here; LCR just evolves
   with the balance sheet) and charted against `config.LCR_REGULATORY_MIN`/`RAS_THRESHOLD`/`INTERNAL_TARGET`.
   `compute_lcr(stressed=True)` additionally scales each outflow category's factor by
   `config.LCR_STRESS_MULTIPLIERS` (capped at 100%) for a simple liquidity stress view, reported
   alongside the base LCR every month and charted against the same threshold lines
   (`lcr_stressed.png`, base rate scenario) - by construction, stressed LCR never exceeds base LCR.
7. **Net Stable Funding Ratio** ([core/nsfr.py](core/nsfr.py)) - NSFR = ASF / RSF, Basel III (BCBS295)
   standard factors reusing tags positions already carry (`hqla_level`, `lcr_outflow_category`,
   `calibration_category`) plus `category_type`/`ladder_months`: capital and stable/less-stable retail
   deposits get flat ASF factors, laddered liabilities (term deposits, term wholesale funding) blend the
   under-1y and >=1y factors by the fraction of their maturity ladder beyond 12 months. On the asset
   side, HQLA gets its RSF haircut (cash 0%, L1 5%, L2A 15%, L2B 50%), performing loans blend by average
   life (`1/cpr_annual`, or a flat under-1y factor for variable-rate/revolving loans with no
   amortization schedule), and `rsf_factor_override` (set on the residential mortgage position) gives
   Basel's preferential mortgage RSF factor without a name lookup. Tracked monthly per scenario, charted
   against `config.NSFR_REGULATORY_MIN`.
8. **Capital-lite (CET1 ratio)** ([core/capital.py](core/capital.py)) - a standardized-approach RWA
   proxy (`Position.rwa_density`, set per position in balance_sheet.yaml: sovereign/cash 0%, agency MBS
   20%, residential mortgage/munis 50%, regulatory-retail consumer loans 75%, corporate/C&I/CRE 100%)
   divided into the equity path the engine already tracks: `cet1_ratio = CET1 / RWA`. Each month's NII
   is split between retained equity and dividends by `config.DIVIDEND_PAYOUT_RATIO` (which supersedes
   the old always-fully-retained `RETENTION_RATIO` - one dividend-policy knob, not two ways to say the
   same thing), so a higher payout ratio visibly slows CET1 accretion. Charted against
   `CET1_REGULATORY_MIN`/`BUFFERED_MIN`/`INTERNAL_TARGET`. This is illustrative, not a real regulatory
   capital calculation: real RWA also includes market and operational risk components (trading book,
   op-risk capital) this stylized balance sheet has no position to represent.
9. **Joint LCR-NIM view** ([core/joint_view.py](core/joint_view.py)) - one table, per position: NIM
   contribution, FTP customer margin, and LCR impact (HQLA contribution for assets, weighted outflow
   contribution for liabilities) side by side, ranked by margin per unit of liquidity cost - which
   products earn the most NIM per dollar of liquidity consumed.
10. **Unified cashflow engine** ([core/cashflows.py](core/cashflows.py)) - one canonical
   coupon-bearing cashflow generator (principal + accrued interest), consumed by both AFS MTM and
   full-revaluation EVE below. Each position's z-spread (a constant spread over the discount curve) is
   solved once so PV equals its book balance at the base curve - the spread has an economic reading
   (the position's credit/liquidity margin over Treasuries), unlike a raw calibration ratio.
11. **AFS mark-to-market** ([core/mtm.py](core/mtm.py)) - `accounting: AFS` positions (Treasuries,
   Municipal & corporate bonds) get a monthly unrealized gain/loss by revaluing their remaining
   cashflow schedule off the scenario curve at their solved z-spread; `accounting: HTM` (Agency MBS)
   accrues only and never shows an MTM figure. Month-0 gain is $0 by construction (the z-spread's
   calibration point), so every later month's gain reflects the curve's movement since then. The MTM
   buffer report caps unrealized gains available for sale at `config.TRADING_LIMIT_PCT` of the HTM book
   (an RBI-style trading-limit convention).
12. **Full-revaluation EVE** ([core/eve.py](core/eve.py)) - EVE = PV(asset cashflows) - PV(liability
   cashflows), on the same coupon-bearing cashflows and z-spreads (solved once against the base curve
   and held constant across every scenario, so a scenario's delta EVE isolates the curve's own
   movement). Administered (NMD) positions use their behavioral-duration replicating ladder for
   principal timing, the same convention as the gap report and pooled_replicating FTP. Because every
   position reprices to book at the base curve by construction, base EVE equals book equity to within
   pennies - a sanity invariant reported alongside each scenario. Run next to the linear duration
   approximation so the convexity difference is visible directly: the linear approximation is
   symmetric by construction (+-D x shock x MV), full revaluation isn't - a falling-rate move typically
   helps EVE more than a rising-rate move of the same size hurts it. Reports the standard six IRRBB
   scenarios: parallel up/down, steepener, flattener, short rate up, short rate down.
13. **Back-testing** ([core/backtest.py](core/backtest.py), `--backtest actuals.csv`) - compares the
    forecast against observed actuals (CSV: `month, actual_nii, actual_avg_earning_assets, actual_nim`),
    with an exact rate/volume/residual attribution of the monthly NII error (the three components sum
    to the total error by algebraic construction, not approximately). The residual is the rate x volume
    interaction term - not attributable to either factor alone, so it's labeled "unmodellable" rather
    than treated as model error. `sample_actuals.csv` (checked into the repo) is generated by running
    the model against itself with perturbed growth rates and a small rate offset: it demonstrates the
    harness's mechanics, not the model's real-world accuracy, since it's circular (the "actuals" are the
    model's own output). For a genuine out-of-sample check, see the real-actuals back-test below.
14. **Real-actuals back-test** ([core/fdic_backtest.py](core/fdic_backtest.py), `--backtest-fdic N`,
    requires `--bank-cert`) - a non-circular alternative to (13): calibrates the balance sheet as of `N`
    quarters ago (reusing the same, now tag-driven, FDIC calibration path - see Data sources below),
    replays the *realized* historical Treasury curve between that quarter and today
    (`data_sources/treasury_curve.py::get_historical_curves`), and compares the model's aggregated
    quarterly NII/NIM against what the bank actually went on to report
    (`data_sources/fdic_history.py`, same FDIC BankFind `/financials` endpoint, queried across
    quarters instead of just the latest one). The model's own month 0 is a seed month calibrated to the
    as-of quarter's own reported figures (the same day-0 invariant used everywhere else in this model),
    so it's dropped before aggregating the remaining months into 3-month quarters for the comparison.
    Real error here is expected and is the point: the model doesn't know the bank's actual future
    growth, mix shifts, or pricing decisions, so read the attribution the same way as (13) rather than
    tuning assumptions to make it look better.
15. **Outputs** ([reporting/](reporting/)) - Excel workbook (one sheet per report) + charts.

## Data sources

- **Rates**: [FRED API](https://fred.stlouisfed.org/docs/api/api_key.html) (free key) anchors the
  starting benchmark rate to the latest real Effective Fed Funds Rate. Without a key, it falls back
  to `config.STARTING_BENCHMARK_RATE`.
- **Balance sheet**: [FDIC BankFind Suite API](https://banks.data.fdic.gov/docs/) (public, no key)
  can calibrate the model's balance sheet to a real bank's latest Call Report totals (loan/securities/
  deposit size and blended yields), rescaling the default mix proportionally. Without a `--bank-cert`,
  it uses the built-in synthetic ~$5B commercial bank calibrated to typical peer-group ratios.
  - Note: FDIC certificate numbers can be stale for banks that were acquired/merged (their Call Report
    history stops at the merger date). Use `--bank-name` to find the right CERT, and check the printed
    "as of" date in the calibration log.
  - Which positions get rescaled to which reported total is driven entirely by each position's
    `calibration_category` tag in `balance_sheet.yaml` (`loans`, `securities`, `cash`, `deposits`,
    `borrowings`, `other` - see `core/balance_sheet.py::CALIBRATION_CATEGORIES`), not by matching
    position names against a hardcoded set: renaming or adding a position can't silently drop it into
    the wrong bucket, which is exactly what happened once before this was tag-driven.
  - `--backtest-fdic N` (requires `--bank-cert`) reuses this same tag-driven calibration path as of a
    past quarter (`data_sources/fdic_history.py::fetch_snapshot`) instead of the latest one, for the
    real-actuals back-test (see "How it works" (12) above).
  - **LCR composition**: FDIC's Call Report totals don't break out HQLA levels or deposit
    outflow-stability mix, so without further help the calibrated LCR just carries over the synthetic
    template's assumed proportions - directionally fine, but not checked against anything real. For
    banks with a checked-in real Basel III Pillar 3 LCR disclosure fixture
    (`data_sources/lcr_disclosures.py`; PNC/CERT 6384 ships as the worked example), the calibration
    additionally reallocates the securities and deposit envelopes to match that bank's own reported
    HQLA composition and deposit outflow mix, and applies its disclosed outflow adjustment percentage
    (a real Basel tailoring-rule modifier for banks below the largest size category). PNC's calibrated
    day-0 LCR lands at 108%, matching its own disclosed figure - see `core/lcr.py` and
    `data_sources/fdic_bank.py::_apply_lcr_disclosure` for the mechanics. Without a fixture, LCR
    calibration is a no-op (`additional_outflow=0`, `outflow_adjustment_pct=1.0`).

## Usage

```bash
pip install -r requirements.txt

# Default run - synthetic balance sheet, no FRED key
python main.py

# Anchor to the real Fed Funds rate
python main.py --fred-api-key YOUR_FRED_KEY

# Find a bank's FDIC certificate number
python main.py --bank-name "First National Bank"

# Full run: real bank + real rate anchor, 24-month horizon
python main.py --bank-cert 12345 --fred-api-key YOUR_FRED_KEY --months 24

# Estimate NMD core/non-core behavior from an actual deposit history instead of assuming it
# (CSV columns: month, product, balance - product must match a "{product} - Core" /
# "{product} - Non-Core" pair in balance_sheet.yaml, e.g. "NOW", "Savings & MMDA")
python main.py --deposit-history my_deposit_history.csv

# Back-test the forecast against observed actuals (CSV columns: month, actual_nii,
# actual_avg_earning_assets, actual_nim) - sample_actuals.csv ships in the repo, but is a
# synthetic, circular demo of the harness, not a real accuracy check (see "How it works" (13))
python main.py --backtest sample_actuals.csv

# Real out-of-sample back-test: calibrate as of 8 quarters ago, replay the realized Treasury
# curve since then, and compare against what the bank actually went on to report
python main.py --bank-cert 6384 --backtest-fdic 8
```

Outputs land in `outputs/`: `nim_forecast.xlsx` (NIM summary, sensitivity, rate sensitivity gap,
duration detail/summary, linear and full-revaluation EVE sensitivity, structural liquidity,
earnings-at-risk, FTP/ALM desk P&L, LCR, NSFR, CET1 capital, joint LCR-NIM view, AFS MTM, back-test vs
actuals (either or both of the CSV-driven and FDIC real-actuals flavors, if requested), and full
per-scenario bucket detail - one sheet each) plus 12 charts.

## FTP policy spread calibration

`python main.py --ftp-recalibrate` runs `core/ftp_calibration.py`: an optimizer that tunes
`config.FTP_CURVE_SPREADS_BY_TENOR_YEARS` to minimize the variance of monthly ALM desk P&L across a
library of stylized historical rate cycles (`curve/historical_cycles.py`: a 2008-style collapse, a
2013-style taper steepening, a 2018-style hiking cycle, a 2020-style crash to zero - illustrative
shapes, not fitted to actual historical data), subject to the short-tenor minimum spread floor
(`FTP_SHORT_TENOR_MIN_SPREAD`). This mirrors an annual FTP policy review: back-test ALM desk P&L
across past rate cycles and recalibrate the curve until desk P&L stays roughly neutral regardless of
which way rates moved. Prints before/after cross-cycle variance and the calibrated spread curve;
tune `config.FTP_CURVE_SPREADS_BY_TENOR_YEARS` directly to apply a manual management overlay instead.

## Worked example: PNC Bank (CERT 6384)

`python main.py --bank-cert 6384 --months 24` calibrates the balance sheet to PNC's actual latest
Call Report totals and shows a real ALM pattern: the repricing gap is *positive* in the 0-1 month
band (short-term assets reprice fast) but sharply *negative* in the 1-12 month bands (NOW/MMDA/CD
repricing lands there), then positive again beyond a year. That shape shows up directly in
Earnings-at-Risk: a +100bps shock *helps* NII over the first few months (asset-sensitive near-term),
crosses zero within roughly six months, and *hurts* NII from there on (liability-sensitive
medium-term) - see `outputs/earnings_at_risk.png`. This is exactly the kind of gap/EaR crossover
real ALCOs watch for, and why a single-horizon EaR number can be misleading on its own. (The exact
crossover month shifts as the model's repricing mechanics get more realistic - it's the pattern
that matters, not the specific month.)

Caveat: the FDIC calibration only rescales *totals and blended yields* to match PNC's real numbers;
the underlying mix (betas, CPRs, deposit duration/decay assumptions) is still the synthetic
community-bank template. For a $500B+ bank with a large trading book, wholesale funding, and active
hedging, treat the *direction* of the gap/EVE/EaR results as more reliable than their exact magnitude -
see "Extending" below for how to tighten this up.

## Extending

- Swap/add scenarios in `config.RATE_SCENARIOS` - each is a curve shift function
  (`curve/shocks.py`), so non-parallel scenarios (steepener, flattener, twist) are
  already supported, not just parallel shocks. A scenario can also be a
  `(shift_fn, basis_shocks)` pair to additionally shock one or more indices'
  overlays (`config.INDEX_BASIS`, `curve/basis.py`) without moving the base curve.
- Add or resize entries in `config.INDEX_BASIS` to give ADMIN or MCLR a basis too
  (currently only SHORT/TENOR/FIXED/TBILL3M are resolved through index_rate; ADMIN
  and MCLR are engine-driven and read the base curve directly, see core/products/
  administered.py and core/indices.py::compute_mclr).
- Add Call Report category-level fields to `data_sources/fdic_bank.py` (`FIELDS`) for a more precise
  position-by-position calibration instead of the current aggregate rescaling.
- `--backtest-fdic` re-plays the *realized rate path* against an as-of calibration, but the
  post-snapshot balance-sheet *growth/mix* still follows `balance_sheet.yaml`'s assumed
  `growth_rate_annual` per position, not the bank's own actual subsequent growth (which the model has
  no way to know in advance) - the volume-variance term in the attribution output is exactly this gap.
- Add another bank's real LCR disclosure to `data_sources/lcr_disclosures.py` (same fields as the
  PNC entry, sourced from that bank's own Pillar 3 LCR/NSFR filing) to ground its calibrated day-0
  LCR the same way, instead of the synthetic template's assumed HQLA/deposit mix.
- Tune `behavioral_duration_years` / `liquidity_decay_annual` per position in `balance_sheet.yaml` -
  these are behavioral assumptions a real ALCO would set from the bank's own deposit studies,
  not derived from the data.
- The NSFR/RWA tables (`core/nsfr.py`, `core/capital.py`) are simplified: laddered positions blend ASF/
  RSF factors off a uniform maturity ladder rather than real contractual maturity buckets, and RWA has
  no market-risk or operational-risk component (real regulatory RWA does). Tune `Position.rwa_density` /
  `rsf_factor_override` per position in `balance_sheet.yaml`, or extend `core/nsfr.py`'s blending rule,
  for a closer match to a specific bank's actual regulatory capital/funding stack.
- For stochastic/Monte Carlo rate paths (vs. today's deterministic shock scenarios), replace
  `curve/scenarios.py`'s path builder with a short-rate model (e.g. Vasicek/CIR) sampled N times,
  and run `core/engine.py` once per path to get a NIM distribution (and EaR/EVE distributions too).
- Tune the MCLR chain in `config.py` (`MCLR_DEPOSIT_WEIGHT`, `MCLR_EQUITY_SPREAD`) or flag more/fewer
  liability positions `feeds_mclr_deposit_cost: true` in `balance_sheet.yaml` to change what counts
  as the marginal cost of new deposit production.
- Tune `refi_sensitivity` / `cpr_max` per `fixed_amortizing` position in `balance_sheet.yaml` to make
  the prepayment burnout effect stronger/weaker, or add it to other amortizing books.
