# EV Adoption in Poland – Analysis of Electric Car Registrations (CEPiK)

> 🚧 Work in progress: data downloaded (212,795 electric vehicles, incl. 159,354 passenger cars,
> registered 2018 – Oct 2026). Next step: data model in PostgreSQL.

## Questions

1. Did the EV subsidy programmes („Mój Elektryk”, „NaszEauto”) translate into registrations?
2. New from a dealer or used from import – how is the market structure changing?
3. Electrification map: EVs per 1,000 residents by county vs. income (Statistics Poland, GUS).
4. Battle of the brands: Tesla vs. Chinese makes.
5. (optional) EVs per charging point (EIPA registry).

## Data

Official CEPiK API (Central Register of Vehicles and Drivers, Ministry of Digital Affairs):
<https://api.cepik.gov.pl>. The project downloads vehicles with `rodzaj-paliwa = ENERGIA ELEKTRYCZNA`
(fuel type: electric) first registered in Poland since 2018, **including deregistered ones**
(`tylko-zarejestrowane=false`), so that earlier years are not undercounted. No personal data.

## How to run

```bash
pip install -r requirements.txt
python download.py        # ~1 day (API rate limits), resumes after interruption
python load_postgres.py   # loads data into PostgreSQL, can be run while downloading
python fetch_gus.py       # reference data from Statistics Poland (counties, TERYT codes)
python run_sql.py         # builds the analytical model (sql/*.sql, in order, with tests)
```

PostgreSQL connection: copy `.env.example` to `.env` and fill in the password
(`.env` is git-ignored).

Download progress: `python progress.py` (or double-click `progress.cmd`) – share of completed
periods, progress per year, number of vehicles and estimated time remaining.

When running in the background, the downloader logs to `download.log`. To stop it, kill the
process – on restart it continues where it left off.

## CEPiK API quirks (handled in `download.py`)

| Problem | Solution |
|---|---|
| Queries over long periods end with HTTP 504 | quarterly queries, automatically split into months when needed |
| The first query for a period usually times out, a retry hits the server-side cache | quick retry after a timeout |
| Responses cut off mid-way (HTTP 200, incomplete JSON) | retry |
| Rate limit reported as 429 **or 404** (`API-ERR-100009`) | detected from the response body, 65 s pause, 10 s between requests |
| `meta.count` is sometimes wrong | paginate using `links.next` |
| `DH_KEY_TOO_SMALL` – server DH key too short for modern OpenSSL | lowered cipher level for this host only (certificate still verified) |
| Bulk CSV files (API and dane.gov.pl) are a 2022 snapshot | download through the API |

## Database (PostgreSQL, schema `cepik`)

- `cepik.vehicles_raw` – all downloaded vehicles, every API field as text, key `id`;
  column names mirror the API (Polish) for traceability
- `cepik.v_vehicles` – typed view (dates, numbers) with English column names and helper columns
  (`is_new`, `is_imported`)
- `cepik.v_passenger_cars` – passenger cars only (raw data also includes mopeds, motorcycles, trucks)
- `cepik.try_date()`, `cepik.try_num()` – safe casts (a bad value gives NULL instead of an error)

Values in the data (e.g. `SAMOCHÓD OSOBOWY`, `LUBELSKIE`) are kept in Polish, as in the source.

## Reference data (PostgreSQL, schema `gus`)

From the GUS Local Data Bank API (<https://bdl.stat.gov.pl>), loaded by `fetch_gus.py`:

- `gus.voivodeship` – 16 voivodeships with TERYT codes
- `gus.county` – 380 counties (66 city, 314 rural) with TERYT codes; historical units
  kept by BDL (e.g. *powiat warszawski*, abolished in 2002) are filtered out

## Analytical model (PostgreSQL, schema `dw`)

Built by `run_sql.py` from the files in `sql/`. Each file ends with tests that fail the build
if the data doesn't add up.

### `dw.dim_location` – where vehicles are registered

**CEPiK records the registration office, not the owner's place of residence** – the owner
location fields are empty for all records. 45 rural counties are served by the office of the
neighbouring city (e.g. *powiat lubelski* is served from Lublin), so CEPiK reports them under
the city's name and there is no separate *LUBELSKI* county in the data. Joining CEPiK with GUS
statistics by county name would silently drop these 45 counties.

| Table | Grain | Purpose |
|---|---|---|
| `dw.dim_location` | registration area (335) | dimension for the fact table; `area_type`: `city`, `rural`, `city + rural` |
| `dw.bridge_location_county` | GUS county (380) | registration area → official counties, to aggregate population, income etc. |
| `dw.county_registration_seat` | rural county (45) | documented manual mapping: rural county → city it is registered under |

Also note: county names are not unique across voivodeships (e.g. *ŚWIDNICKI* exists in
Lubelskie and Dolnośląskie), so locations are always identified by voivodeship + county.

---

🇵🇱 **Po polsku:** analiza rejestracji samochodów elektrycznych w Polsce na podstawie danych
CEPiK – wpływ dopłat, rynek nowych i używanych aut, mapa elektryfikacji powiatów i rywalizacja marek.
Stack: Python, PostgreSQL, Power BI.
