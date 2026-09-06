-- ============================================================
--  WeatherLLM relational schema
--  Source: National Weather Service API (api.weather.gov)
--
--  Reference (slow-changing)  : states, offices, locations, stations
--  Event / time-series (grows): observations, forecasts, alerts
--
--  Every table uses the natural key supplied by (or deterministically
--  derived from) the API, so re-running ingestion is idempotent.
-- ============================================================


-- ---------- REFERENCE DATA ----------

-- US states / territories. Alerts are published per state, and every
-- location sits in one, so this is the dimension that joins the two.
CREATE TABLE states (
    code TEXT PRIMARY KEY,          -- 'WA', 'TX'  (natural key from NWS)
    name TEXT
);


-- NWS Weather Forecast Offices. /points/{lat},{lon} returns the office
-- ("gridId") responsible for a coordinate.
CREATE TABLE offices (
    idOffice   TEXT PRIMARY KEY,    -- 'SEW', 'OKX'  (natural key)
    name       TEXT,
    stateCode  TEXT,
    FOREIGN KEY (stateCode) REFERENCES states(code)
);


-- The cities we track. Configured in src/ingest.py, enriched from /points.
CREATE TABLE locations (
    idLocation TEXT PRIMARY KEY,    -- slug, e.g. 'seattle-wa'
    name       TEXT NOT NULL,
    stateCode  TEXT,
    latitude   REAL,
    longitude  REAL,
    idOffice   TEXT,
    gridX      INTEGER,
    gridY      INTEGER,
    FOREIGN KEY (stateCode) REFERENCES states(code),
    FOREIGN KEY (idOffice)  REFERENCES offices(idOffice)
);


-- Physical observation stations, one chosen per location (the nearest).
CREATE TABLE stations (
    idStation  TEXT PRIMARY KEY,    -- 'KSEA'  (natural key)
    name       TEXT,
    latitude   REAL,
    longitude  REAL,
    idLocation TEXT,
    FOREIGN KEY (idLocation) REFERENCES locations(idLocation)
);


-- ---------- TIME-SERIES / EVENT DATA ----------

-- One row per station reading. Grows on every fetch.
-- Natural key: station + observation timestamp (the API has no single id),
-- so re-fetching the same reading collides and is ignored rather than
-- duplicated.
CREATE TABLE observations (
    idObservation  TEXT PRIMARY KEY,  -- '<idStation>@<observedAt>'
    idStation      TEXT NOT NULL,
    observedAt     TEXT,
    temperatureC   REAL,
    dewpointC      REAL,
    humidity       REAL,
    windSpeedKmh   REAL,
    windDirection  REAL,
    pressurePa     REAL,
    visibilityM    REAL,
    textDescription TEXT,             -- short prose, e.g. 'Mostly Cloudy'
    fetchedAt      TEXT NOT NULL,
    FOREIGN KEY (idStation) REFERENCES stations(idStation)
);


-- Forecast periods. detailedForecast is the narrative text the
-- embedding layer consumes. Grows on every fetch.
-- Natural key: location + period start time.
CREATE TABLE forecasts (
    idForecast      TEXT PRIMARY KEY, -- '<idLocation>@<startTime>'
    idLocation      TEXT NOT NULL,
    periodName      TEXT,             -- 'Tonight', 'Tuesday'
    startTime       TEXT,
    endTime         TEXT,
    isDaytime       INTEGER,
    temperature     REAL,
    temperatureUnit TEXT,
    windSpeed       TEXT,
    windDirection   TEXT,
    shortForecast   TEXT,
    detailedForecast TEXT,            -- <- embedded narrative
    fetchedAt       TEXT NOT NULL,
    FOREIGN KEY (idLocation) REFERENCES locations(idLocation)
);


-- Active watches / warnings / advisories. description + instruction are
-- multi-paragraph prose and are the richest text in the whole dataset.
-- Natural key: the CAP identifier supplied by NWS.
CREATE TABLE alerts (
    idAlert     TEXT PRIMARY KEY,     -- NWS CAP id (natural key)
    stateCode   TEXT,
    event       TEXT,                 -- 'Winter Storm Warning'
    severity    TEXT,
    urgency     TEXT,
    certainty   TEXT,
    headline    TEXT,
    areaDesc    TEXT,
    effective   TEXT,
    expires     TEXT,
    description TEXT,                 -- <- embedded narrative
    instruction TEXT,                 -- <- embedded narrative
    fetchedAt   TEXT NOT NULL,
    FOREIGN KEY (stateCode) REFERENCES states(code)
);


-- ---------- INDEXES ----------

CREATE INDEX idx_observations_station ON observations(idStation, observedAt);
CREATE INDEX idx_forecasts_location   ON forecasts(idLocation, startTime);
CREATE INDEX idx_alerts_state         ON alerts(stateCode, expires);
CREATE INDEX idx_locations_state      ON locations(stateCode);
