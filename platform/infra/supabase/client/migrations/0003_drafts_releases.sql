-- 0003: the client's draft workflow.json and its published releases (#23). zm_api gets read + write
-- through 0001's default privileges in schema app.

-- exactly one draft per client vault: what the agent edits (S2-03) and what publish compiles
create table app.drafts (
  id           smallint primary key default 1 check (id = 1),
  workflow     jsonb not null,
  template_key text,
  updated_at   timestamptz not null default now(),
  updated_by   uuid
);

-- one row per publish that produced a new Operaton deployment; immutable once written
create table app.releases (
  version       int primary key check (version >= 1),
  deployment_id text not null unique,             -- Operaton deployment holding every process of it
  workflow      jsonb not null,                    -- the draft as published
  forms         jsonb not null,                    -- form key -> {schema, layout} (#19)
  ui            jsonb not null,                    -- the apps section (D19), never in BPMN
  routing_vars  jsonb not null,                    -- form key -> variables sent to Operaton (D5)
  process_keys  jsonb not null,                    -- flow key -> main process key
  blocks_used   jsonb not null default '{}',       -- block -> manifest version (null until #30/#92)
  sha256        text not null,                     -- Release.digest()
  published_by  uuid,                              -- platform owner (platform.owners.id)
  published_at  timestamptz not null default now(),
  active        boolean not null default false
);
create unique index releases_one_active on app.releases (active) where active;
