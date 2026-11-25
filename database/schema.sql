-- California LIHTC grocery-amenity dataset. PostgreSQL 15+ with PostGIS 3.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE data_sources (
    source_id        text PRIMARY KEY,           -- matches configs/sources.yaml keys
    owner            text,
    url              text NOT NULL,
    access_method    text,
    license          text,
    reliability      text CHECK (reliability IN ('high','medium-high','medium','low')),
    notes            text
);

-- Untouched payloads, one row per source record per fetch.
CREATE TABLE raw_records (
    raw_id           bigserial PRIMARY KEY,
    source_id        text NOT NULL REFERENCES data_sources,
    source_record_id text NOT NULL,              -- e.g. 'way/30265679', permit number
    snapshot_sha256  text NOT NULL,              -- ties back to data/raw/<source>/manifest.jsonl
    retrieved_at     timestamptz NOT NULL,
    payload          jsonb NOT NULL,
    UNIQUE (source_id, source_record_id, snapshot_sha256)
);

CREATE TABLE stores (
    store_id         text PRIMARY KEY,           -- e.g. 'CA_OC_000123'
    canonical_name   text NOT NULL,
    brand            text,
    store_number     text,
    address_norm     text,
    city             text,
    is_multipurpose  boolean NOT NULL DEFAULT false,
    location         geometry(Point, 4326) NOT NULL,
    outline          geometry(Polygon, 4326),    -- exterior wall: TCAC measures to it in centres
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX stores_location_gix ON stores USING gist (location);

-- Which raw records were resolved to which store, and how.
CREATE TABLE store_source_links (
    store_id         text REFERENCES stores,
    raw_id           bigint REFERENCES raw_records,
    match_method     text NOT NULL,              -- source_id | address_exact | name_spatial | manual
    match_score      real,
    PRIMARY KEY (store_id, raw_id)
);

CREATE TABLE buildings (
    building_id      text PRIMARY KEY,           -- '<source_id>:<OBJECTID>'
    source_id        text NOT NULL REFERENCES data_sources,
    site_address     text,
    footprint_sqft   numeric,
    geometry         geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX buildings_geom_gix ON buildings USING gist (geometry);

-- Field-level provenance: every attribute value, with why we believe it.
CREATE TABLE attribute_evidence (
    evidence_id         bigserial PRIMARY KEY,
    store_id            text NOT NULL REFERENCES stores,
    attribute           text NOT NULL,           -- floor_area_sqft | sells_fresh_meat | operating | ...
    value               jsonb NOT NULL,
    source_id           text REFERENCES data_sources,
    raw_id              bigint REFERENCES raw_records,
    source_type         text NOT NULL,           -- see area_evidence_strength in eligibility_rules.yaml
    measurement_type    text,                    -- gross_interior | grocery_section | leased | footprint
    strength            text NOT NULL CHECK (strength IN ('authoritative','strong','proxy','prior')),
    retrieved_at        timestamptz NOT NULL,
    verification_status text NOT NULL DEFAULT 'unverified'
                        CHECK (verification_status IN ('unverified','verified','rejected')),
    verified_by         text,
    note                text
);
CREATE INDEX attribute_evidence_store_attr ON attribute_evidence (store_id, attribute);

CREATE TABLE eligibility_results (
    store_id          text NOT NULL REFERENCES stores,
    rules_version     text NOT NULL,             -- regulation version_date
    store_class       text CHECK (store_class IN ('full_scale_supermarket','neighborhood_market')),
    status            text NOT NULL CHECK (status IN ('eligible','likely','ineligible','unverified')),
    area_basis_sqft   numeric,
    evidence_ids      bigint[] NOT NULL DEFAULT '{}',
    reasons           text[] NOT NULL,
    evaluated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, rules_version)
);

-- Spatial join used by the pipeline: footprint containing each store point.
-- (Footprint area from the server is in EPSG:2230 sq ft; recomputing on
--  geography gives m^2, hence the 10.7639 factor.)
CREATE VIEW store_footprint_candidates AS
SELECT s.store_id, b.building_id,
       ST_Area(b.geometry::geography) * 10.7639 AS footprint_sqft
FROM stores s
JOIN buildings b ON ST_Intersects(b.geometry, s.location);
