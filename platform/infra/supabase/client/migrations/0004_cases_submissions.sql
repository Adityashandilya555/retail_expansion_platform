-- 0004: cases, task submissions and the values they recorded (#26). zm_api gets read + write
-- through 0001's default privileges in schema app. Only routing variables reach Operaton (D5);
-- every value a person enters lives here.

-- one row per started case, of any flow (D13); `code` is also the Operaton business key
create table app.cases (
  id                  uuid primary key default gen_random_uuid(),
  flow_key            text not null,
  code                text not null unique,
  release_version     int not null references app.releases (version),
  process_instance_id text not null unique,
  created_by          uuid not null,
  created_at          timestamptz not null default now()
);

-- the `{seq:N}` part of each flow's case code; gaps are fine, duplicates are not
create table app.case_counters (
  flow_key text primary key,
  last     int not null
);

-- one row per task submission: pending → completed (Operaton took it) → committed (values
-- written); failed when Operaton refused it. The worker's reconciler finishes stuck ones.
create table app.submissions (
  id               uuid primary key default gen_random_uuid(),
  case_id          uuid not null references app.cases (id) on delete cascade,
  task_def_key     text not null,
  operaton_task_id text not null,
  form_key         text not null,
  release_version  int not null,
  values           jsonb not null,
  outcome          text,
  comment          text,
  status           text not null default 'pending'
                   check (status in ('pending', 'completed', 'committed', 'failed')),
  attempts         int not null default 0,      -- completions retried by the reconciler
  last_error       text,
  submitted_by     uuid not null,
  idempotency_key  text not null unique,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index submissions_unfinished on app.submissions (updated_at)
  where status in ('pending', 'completed');

-- every value recorded for a case: from its start form (submission_id null) or a submission
create table app.case_values (
  id              bigint generated always as identity primary key,
  case_id         uuid not null references app.cases (id) on delete cascade,
  field           text not null,
  value           jsonb not null,
  release_version int not null,
  submission_id   uuid references app.submissions (id) on delete cascade,
  at              timestamptz not null default now(),
  unique (submission_id, field)
);
create index case_values_latest on app.case_values (case_id, field, at desc);
