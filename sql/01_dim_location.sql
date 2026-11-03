-- =====================================================================================
-- dim_location: vehicle registration areas, linked to official counties (GUS / TERYT)
--
-- CEPiK records WHERE A VEHICLE WAS REGISTERED (the county registration office), not
-- where the owner lives - the owner location fields are empty for all records.
-- 45 rural counties are served by the office of the neighbouring city county
-- (e.g. powiat lubelski is served from Lublin), so CEPiK reports them together
-- under the city's name. One registration area = one or two GUS counties.
--
-- Tables:
--   dw.county_registration_seat  rural county -> city county it is registered under
--   dw.dim_location              one row per registration area seen in CEPiK
--   dw.bridge_location_county    registration area -> GUS counties (for population, income...)
--
-- Requires: cepik.v_vehicles (load_postgres.py), gus.county (fetch_gus.py)
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS dw;

DROP TABLE IF EXISTS dw.bridge_location_county;
DROP TABLE IF EXISTS dw.dim_location CASCADE;
DROP TABLE IF EXISTS dw.county_registration_seat;


-- -------------------------------------------------------------------------------------
-- 1. Rural counties registered under a city county.
--    Found as GUS counties with no matching name in CEPiK; all 45 are rural counties
--    whose administrative seat is the city. Seat cities were resolved from gus.county
--    by name and verified to be unique within the voivodeship.
-- -------------------------------------------------------------------------------------
CREATE TABLE dw.county_registration_seat (
    rural_county_teryt  char(4) PRIMARY KEY REFERENCES gus.county,
    seat_county_teryt   char(4) NOT NULL UNIQUE REFERENCES gus.county
);

INSERT INTO dw.county_registration_seat (rural_county_teryt, seat_county_teryt) VALUES
    ('0206', '0261'),   -- karkonoski       -> Jelenia Góra
    ('0209', '0262'),   -- legnicki         -> Legnica
    ('0221', '0265'),   -- wałbrzyski       -> Wałbrzych
    ('0223', '0264'),   -- wrocławski       -> Wrocław
    ('0403', '0461'),   -- bydgoski         -> Bydgoszcz
    ('0406', '0462'),   -- grudziądzki      -> Grudziądz
    ('0415', '0463'),   -- toruński         -> Toruń
    ('0418', '0464'),   -- włocławski       -> Włocławek
    ('0601', '0661'),   -- bialski          -> Biała Podlaska
    ('0603', '0662'),   -- chełmski         -> Chełm
    ('0609', '0663'),   -- lubelski         -> Lublin
    ('0620', '0664'),   -- zamojski         -> Zamość
    ('0801', '0861'),   -- gorzowski        -> Gorzów Wielkopolski
    ('0809', '0862'),   -- zielonogórski    -> Zielona Góra
    ('1006', '1061'),   -- łódzki wschodni  -> Łódź
    ('1010', '1062'),   -- piotrkowski      -> Piotrków Trybunalski
    ('1015', '1063'),   -- skierniewicki    -> Skierniewice
    ('1206', '1261'),   -- krakowski        -> Kraków
    ('1210', '1262'),   -- nowosądecki      -> Nowy Sącz
    ('1216', '1263'),   -- tarnowski        -> Tarnów
    ('1415', '1461'),   -- ostrołęcki       -> Ostrołęka
    ('1419', '1462'),   -- płocki           -> Płock
    ('1425', '1463'),   -- radomski         -> Radom
    ('1426', '1464'),   -- siedlecki        -> Siedlce
    ('1609', '1661'),   -- opolski          -> Opole
    ('1807', '1861'),   -- krośnieński      -> Krosno
    ('1813', '1862'),   -- przemyski        -> Przemyśl
    ('1816', '1863'),   -- rzeszowski       -> Rzeszów
    ('1820', '1864'),   -- tarnobrzeski     -> Tarnobrzeg
    ('2002', '2061'),   -- białostocki      -> Białystok
    ('2007', '2062'),   -- łomżyński        -> Łomża
    ('2012', '2063'),   -- suwalski         -> Suwałki
    ('2212', '2263'),   -- słupski          -> Słupsk
    ('2402', '2461'),   -- bielski          -> Bielsko-Biała
    ('2404', '2464'),   -- częstochowski    -> Częstochowa
    ('2405', '2466'),   -- gliwicki         -> Gliwice
    ('2412', '2473'),   -- rybnicki         -> Rybnik
    ('2604', '2661'),   -- kielecki         -> Kielce
    ('2804', '2861'),   -- elbląski         -> Elbląg
    ('2814', '2862'),   -- olsztyński       -> Olsztyn
    ('3007', '3061'),   -- kaliski          -> Kalisz
    ('3010', '3062'),   -- koniński         -> Konin
    ('3013', '3063'),   -- leszczyński      -> Leszno
    ('3021', '3064'),   -- poznański        -> Poznań
    ('3209', '3261');   -- koszaliński      -> Koszalin


-- -------------------------------------------------------------------------------------
-- 2. Registration areas
-- -------------------------------------------------------------------------------------
CREATE TABLE dw.dim_location (
    location_key        smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voivodeship_teryt   char(2)  NOT NULL REFERENCES gus.voivodeship,
    voivodeship_name    text     NOT NULL,              -- e.g. 'Lubelskie'
    cepik_county        text     NOT NULL,              -- as in CEPiK, e.g. 'LUBLIN' (join key for facts)
    area_name           text     NOT NULL,              -- e.g. 'Lublin + powiat lubelski'
    area_type           text     NOT NULL CHECK (area_type IN ('city', 'rural', 'city + rural')),
    main_county_teryt   char(4)  NOT NULL UNIQUE REFERENCES gus.county,
    n_counties          smallint NOT NULL,
    UNIQUE (voivodeship_teryt, cepik_county)
);

INSERT INTO dw.dim_location
    (voivodeship_teryt, voivodeship_name, cepik_county, area_name, area_type, main_county_teryt, n_counties)
WITH cepik_area AS (
    SELECT DISTINCT api_voivodeship_code AS voivodeship_teryt, county AS cepik_county
    FROM cepik.v_vehicles
    WHERE api_voivodeship_code <> 'XX'
),
main_county AS (        -- the GUS county whose name matches the CEPiK area
    SELECT a.voivodeship_teryt, a.cepik_county, g.teryt, g.short_name, g.county_type
    FROM cepik_area a
    JOIN gus.county g
      ON g.voivodeship_teryt = a.voivodeship_teryt
     AND upper(g.short_name) = a.cepik_county
)
SELECT
    m.voivodeship_teryt,
    initcap(v.name),
    m.cepik_county,
    CASE
        WHEN r.teryt IS NOT NULL       THEN m.short_name || ' + powiat ' || r.short_name
        WHEN m.county_type = 'city'    THEN m.short_name
        ELSE 'powiat ' || m.short_name
    END,
    CASE WHEN r.teryt IS NOT NULL THEN 'city + rural' ELSE m.county_type END,
    m.teryt,
    CASE WHEN r.teryt IS NOT NULL THEN 2 ELSE 1 END
FROM main_county m
JOIN gus.voivodeship v ON v.teryt = m.voivodeship_teryt
LEFT JOIN dw.county_registration_seat s ON s.seat_county_teryt = m.teryt
LEFT JOIN gus.county r ON r.teryt = s.rural_county_teryt
ORDER BY m.voivodeship_teryt, m.cepik_county;


-- -------------------------------------------------------------------------------------
-- 3. Bridge: registration area -> GUS counties
--    The primary key on county_teryt guarantees each county belongs to exactly one area.
-- -------------------------------------------------------------------------------------
CREATE TABLE dw.bridge_location_county (
    county_teryt    char(4)  PRIMARY KEY REFERENCES gus.county,
    location_key    smallint NOT NULL REFERENCES dw.dim_location
);

INSERT INTO dw.bridge_location_county (county_teryt, location_key)
SELECT main_county_teryt, location_key
FROM dw.dim_location
UNION ALL
SELECT s.rural_county_teryt, d.location_key
FROM dw.county_registration_seat s
JOIN dw.dim_location d ON d.main_county_teryt = s.seat_county_teryt;


-- -------------------------------------------------------------------------------------
-- 4. Tests - the script fails if the mapping is incomplete
-- -------------------------------------------------------------------------------------
DO $$
DECLARE n int;
BEGIN
    -- every CEPiK registration area must be in dim_location (an inner join above could drop it)
    SELECT count(*) INTO n
    FROM (SELECT DISTINCT api_voivodeship_code, county FROM cepik.v_vehicles
          WHERE api_voivodeship_code <> 'XX') a
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_location d
                      WHERE d.voivodeship_teryt = a.api_voivodeship_code AND d.cepik_county = a.county);
    IF n > 0 THEN RAISE EXCEPTION '% CEPiK registration area(s) missing from dw.dim_location', n; END IF;

    -- every official county must belong to some registration area
    SELECT count(*) INTO n
    FROM gus.county c
    WHERE NOT EXISTS (SELECT 1 FROM dw.bridge_location_county b WHERE b.county_teryt = c.teryt);
    IF n > 0 THEN RAISE EXCEPTION '% GUS county(ies) not mapped to any registration area', n; END IF;

    -- every rural county from the manual list must have ended up under its seat city
    SELECT count(*) INTO n
    FROM dw.county_registration_seat s
    WHERE NOT EXISTS (SELECT 1 FROM dw.dim_location d WHERE d.main_county_teryt = s.seat_county_teryt);
    IF n > 0 THEN RAISE EXCEPTION '% seat city(ies) from the manual mapping not found in CEPiK', n; END IF;
END $$;
