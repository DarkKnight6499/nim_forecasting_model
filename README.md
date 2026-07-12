# Net Interest Margin & ALM Forecasting Model

A monthly, dynamic asset/liability (ALM) simulation that projects a bank's
Net Interest Margin under multiple interest-rate scenarios, plus the standard
bank treasury/ALCO risk suite built on the same balance sheet: interest rate
sensitivity gap, duration gap & EVE sensitivity, a structural liquidity
statement, and earnings-at-risk.

## How it works

1. **Balance sheet buckets** ([config.py](config.py)) - each asset/liability line
   (loans, securities, deposits, borrowings) has a starting balance, rate, and a
   repricing behavior:
   - `variable` - reprices with the benchmark rate x beta, on a fixed cadence
     (`reset_frequency_months`; default 1 = every month). C&I loans use a 3-month reset,
     mirroring external-benchmark-linked floating loans that reset quarterly rather than
     continuously (e.g. RLLR/MCLR-style reset tenors) rather than one blanket "variable" bucket.
   - `administered` - bank-controlled rate, partial/lagged repricing (e.g. savings, MMDA).
     NOW and Savings/MMDA are each split into **core** (stable, seasonally-patterned, sticky -
     long `duration_years`, slow `liquidity_decay_annual`) and **non-core** (volatile/rate-shopped -
     short duration, fast decay) sub-buckets - standard non-maturity-deposit (NMD) behavioral
     modeling. `seasonal=True` applies `config.SEASONALITY_INDEX_DEPOSITS` to that bucket's growth.
   - `fixed_amortizing` - runs off via a constant prepayment rate (CPR); runoff + growth
     is replaced by new production priced at benchmark + spread (e.g. CRE, mortgage, consumer loans)
   - `laddered` - 1/N of the balance matures and renews each month at benchmark + spread
     (e.g. securities, CDs, term debt)
2. **Rate scenarios** ([model/scenarios.py](model/scenarios.py)) - base/flat, ±100bps, ±200bps,
   ramped in linearly over 12 months (configurable), applied to a benchmark rate proxy.
3. **Dynamic engine** ([model/engine.py](model/engine.py)) - steps every bucket forward month by
   month with growth, computes interest income/expense, and annualized
   NIM = (interest income − interest expense) × 12 / avg earning assets.
4. **Static ALM reports** ([model/alm_reports.py](model/alm_reports.py)) - point-in-time snapshots
   of the *current* balance sheet (no growth), the classic complement to the dynamic engine above:
   - **Rate Sensitivity Gap**: RSA/RSL and cumulative gap across 7 repricing time bands
   - **Duration Gap & EVE Sensitivity**: effective duration per bucket, DGAP = D_A − (L/A)·D_L,
     and ΔEVE (economic value of equity) under each instantaneous rate shock, as % of capital
   - **Structural Liquidity Statement**: inflow/outflow cashflow gap by time band, using
     core-deposit decay (not repricing lag) for non-maturity deposits
   - **Earnings-at-Risk**: cumulative NII impact vs. base at 3/6/12/24-month horizons, plus the
     full monthly delta series - a bank can be asset-sensitive near-term and liability-sensitive
     further out, so a single-horizon number can hide a real crossover (see the PNC example below)
5. **FTP / ALM desk P&L** ([model/ftp.py](model/ftp.py)) - matched-maturity Funds Transfer Pricing:
   every bucket is charged/credited a transfer rate (benchmark + a tenor-based spread, using the
   same effective-duration tenor as the EVE calc), splitting total NII into **customer margin**
   (what business units earn vs. the internal transfer price) and **ALM/Treasury desk P&L** (the
   transfer-pricing net - this *is* "ALM NII"). The two always reconcile exactly to total NII by
   construction (checked every run). Also reports ALM desk P&L stability across rate scenarios -
   a well-calibrated FTP curve keeps this roughly flat; the default curve here doesn't (see below).
6. **Outputs** ([reporting/](reporting/)) - Excel workbook (one sheet per report) + 9 charts.

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
```

Outputs land in `outputs/`: `nim_forecast.xlsx` (NIM summary, sensitivity, rate sensitivity gap,
duration detail/summary, EVE sensitivity, structural liquidity, earnings-at-risk, FTP/ALM desk P&L,
and full per-scenario bucket detail - one sheet each) plus 9 charts.

## FTP curve calibration isn't neutral by default - and that's the point

The `python main.py` run prints "ALM Desk P&L stability across rate scenarios": with the default
`FTP_CURVE_SPREADS_BY_TENOR_YEARS`, ALM desk P&L swings meaningfully across scenarios (e.g. ~$4.2M/mo
under -200bps vs. ~$9.2M/mo under +200bps on the synthetic book) rather than staying flat. That's
expected - this is a simple static spread curve, not one calibrated against the book's actual
duration mismatch. In practice this is exactly what an annual FTP policy review is for: back-test
ALM P&L across historical rate cycles and recalibrate the curve (or apply a management overlay on
specific tenors) until desk P&L stays roughly neutral regardless of which way rates move. Tune
`config.FTP_CURVE_SPREADS_BY_TENOR_YEARS` / `FTP_SHORT_TENOR_MIN_SPREAD` to see the effect.

## Worked example: PNC Bank (CERT 6384)

`python main.py --bank-cert 6384 --months 24` calibrates the balance sheet to PNC's actual latest
Call Report totals and shows a real ALM pattern: the repricing gap is *positive* in the 0-1 month
band (short-term assets reprice fast) but sharply *negative* in the 1-12 month bands (NOW/MMDA/CD
repricing lands there), then positive again beyond a year. That shape shows up directly in
Earnings-at-Risk: a +100bps shock *helps* NII in months 1-6 (asset-sensitive near-term), crosses
zero around month 11-12, and *hurts* NII from month 12 on (liability-sensitive medium-term) - see
`outputs/earnings_at_risk.png`. This is exactly the kind of gap/EaR crossover real ALCOs watch for,
and why a single-horizon EaR number can be misleading on its own.

Caveat: the FDIC calibration only rescales *totals and blended yields* to match PNC's real numbers;
the underlying mix (betas, CPRs, deposit duration/decay assumptions) is still the synthetic
community-bank template. For a $500B+ bank with a large trading book, wholesale funding, and active
hedging, treat the *direction* of the gap/EVE/EaR results as more reliable than their exact magnitude -
see "Extending" below for how to tighten this up.

## Extending

- Swap/add scenarios in `config.RATE_SCENARIOS` (e.g. non-parallel yield curve twists).
- Add Call Report category-level fields to `data_sources/fdic_bank.py` (`FIELDS`) for a more precise
  bucket-by-bucket calibration instead of the current aggregate rescaling.
- Tune `duration_years` / `liquidity_decay_annual` per bucket in `config.py` - these are behavioral
  assumptions a real ALCO would set from the bank's own deposit studies, not derived from the data.
- For stochastic/Monte Carlo rate paths (vs. today's deterministic shock scenarios), replace
  `model/scenarios.py`'s path builder with a short-rate model (e.g. Vasicek/CIR) sampled N times,
  and run `model/engine.py` once per path to get a NIM distribution (and EaR/EVE distributions too).
