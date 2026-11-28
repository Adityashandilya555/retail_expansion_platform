-- 0012: uploaded files (#51, S2-10). The bytes live in the client's private bucket `files`; this
-- table keeps each object's key and sha256, never a URL (signed URLs are made per request).
-- zm_api gets read + write through 0001's default privileges in schema app.

-- one row per upload: pending (signed upload URL issued) → confirmed (streamed back, size, sha256
-- and type checked) or rejected (the object was deleted). A confirmed upload gets its version when
-- a submission or the case's start commits it, so one nobody submits never lists as the case's.
-- Old versions are never deleted (S3-D1).
create table app.files (
  id           uuid primary key default gen_random_uuid(),
  -- null until the case started from a start form claims it (S3-D3)
  case_id      uuid references app.cases (id) on delete cascade,
  flow_key     text not null,
  task_def_key text not null,                   -- 'start' for a start form
  field        text not null,
  key          text not null unique,            -- cases/<code>/<task>/<field>/<uuid>-<name>
  name         text not null,                   -- as the user named it
  size_bytes   bigint not null,                 -- declared at upload, measured at confirm
  sha256       text,
  content_type text,
  version      int,                             -- per (case, field), set when committed
  status       text not null default 'pending'
               check (status in ('pending', 'confirmed', 'rejected')),
  uploaded_by  uuid not null,
  created_at   timestamptz not null default now(),
  confirmed_at timestamptz,
  check (status = 'confirmed' or version is null),
  check (status <> 'confirmed' or sha256 is not null)
);
create unique index files_versions on app.files (case_id, field, version) where status = 'confirmed';
