-- =====================================================================================
-- make_fix: makes for passenger cars registered with an empty make
--
-- 7,451 passenger cars (4.7%) have no make in CEPiK. Almost all of them are new cars
-- from brands that entered the Polish market in 2024-2026 (Leapmotor, Cupra, Omoda,
-- Xpeng, GAC...) - most likely missing from the CEPiK make dictionary, so the make field
-- was left empty and only the model was typed in.
--
-- The make is assigned from the model name, by hand, only when the model is unambiguous
-- and the curb weight matches the real car. Not mapped on purpose:
--   500, 600E           Fiat or Abarth - both sell an electric 500 / 600
--   ET5, ET, ES, N8, E5 short codes used by several Chinese makes
--   ---                 no model given
-- These stay unknown. Power (power_kw) is NOT used for verification: for EVs it holds
-- either peak or 30-minute power depending on who registered the car (e.g. OMODA 5 EV:
-- 161 kW vs 67 kW, same curb weight).
--
-- Naming: makes as sold in Poland (OMODA, JAECOO, not their owner CHERY); if a make
-- already exists in CEPiK, its spelling is reused (SSANGYONG, not the new name KGM).
--
-- Requires: cepik.v_passenger_cars (load_postgres.py)
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS dw;

DROP TABLE IF EXISTS dw.make_fix;

-- one row per model spelling exactly as in CEPiK; applies only where make is empty
CREATE TABLE dw.make_fix (
    cepik_model     text PRIMARY KEY,
    make            text NOT NULL
);

INSERT INTO dw.make_fix (cepik_model, make) VALUES
    ('T03',                     'LEAPMOTOR'),
    ('B03X',                    'LEAPMOTOR'),
    ('B05',                     'LEAPMOTOR'),
    ('B10',                     'LEAPMOTOR'),
    ('C10',                     'LEAPMOTOR'),

    ('TAVASCAN',                'CUPRA'),
    ('BORN',                    'CUPRA'),
    ('BORN 140 KW 58/61 KWH',   'CUPRA'),
    ('BORN 150 KW 58/62 KWH',   'CUPRA'),
    ('BORN 150 KW 60/63 KWH',   'CUPRA'),
    ('BORN 170 KW 58/62 KWH',   'CUPRA'),
    ('BORN 170 KW 60/63 KWH',   'CUPRA'),
    ('BORN 170 KW 77/82 KWH',   'CUPRA'),
    ('BORN 170 KW 79/84 KWH',   'CUPRA'),
    ('BORN 240 KW 79/84 KWH',   'CUPRA'),
    ('RAVAL 155KW',             'CUPRA'),
    ('RAVAL 166KW',             'CUPRA'),

    ('OMODA 5 EV',              'OMODA'),
    ('OMODA5 EV',               'OMODA'),
    ('JAECOO5 EV',              'JAECOO'),

    ('G6',                      'XPENG'),
    ('G9',                      'XPENG'),
    ('G9 AWD PERFORMANCE',      'XPENG'),
    ('P7',                      'XPENG'),
    ('P7I',                     'XPENG'),

    ('AION UT',                 'GAC'),
    ('AION V',                  'GAC'),
    ('AION Y PLUS',             'GAC'),
    ('HYPTEC HT',               'GAC'),

    ('SU7',                     'XIAOMI'),
    ('SU7 MAX',                 'XIAOMI'),
    ('SU7 PRO',                 'XIAOMI'),
    ('SU7 ULTRA',               'XIAOMI'),
    ('YU7',                     'XIAOMI'),
    ('YU7 MAX',                 'XIAOMI'),
    ('YU7MAX',                  'XIAOMI'),

    ('AIR',                     'LUCID'),
    ('TORRES EVX',              'SSANGYONG'),   -- also registered with make SSANGYONG
    ('TGE',                     'MAN'),         -- also registered with make MAN
    ('E.GO LIFE',               'E.GO'),
    ('E.GO LIFE FIRST EDITION', 'E.GO'),
    ('U5',                      'AIWAYS'),
    ('E-HS9',                   'HONGQI'),
    ('E-HS9 PRESIDENT',         'HONGQI');


-- -------------------------------------------------------------------------------------
-- Tests
-- -------------------------------------------------------------------------------------
DO $$
DECLARE n int; n_empty int; n_fixed int;
BEGIN
    -- every mapped spelling must exist in the data with an empty make (catches typos)
    SELECT count(*) INTO n
    FROM dw.make_fix f
    WHERE NOT EXISTS (SELECT 1 FROM cepik.v_passenger_cars c
                      WHERE c.model = f.cepik_model AND c.make IS NULL);
    IF n > 0 THEN RAISE EXCEPTION '% model(s) in dw.make_fix not found with an empty make', n; END IF;

    SELECT count(*), count(f.make) INTO n_empty, n_fixed
    FROM cepik.v_passenger_cars c
    LEFT JOIN dw.make_fix f ON f.cepik_model = c.model
    WHERE c.make IS NULL;
    RAISE INFO 'Cars with empty make: %, fixed: % (% percent)', n_empty, n_fixed, round(100.0 * n_fixed / n_empty, 1);
END $$;
