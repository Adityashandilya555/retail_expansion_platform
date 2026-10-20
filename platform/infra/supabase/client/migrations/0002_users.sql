-- 0002: people who sign in to this client's app (#22). zm_api gets read + write through 0001's
-- default privileges in schema app.

-- case-insensitive emails; Supabase ships citext in `extensions`, a plain Postgres may not have it yet
create schema if not exists extensions;
create extension if not exists citext with schema extensions;

create table app.users (
  id              uuid primary key default gen_random_uuid(),
  email           extensions.citext not null unique,
  name            text not null,
  password_hash   text,                               -- argon2id; null while invited
  status          text not null default 'invited' check (status in ('invited', 'active', 'deactivated')),
  is_client_admin boolean not null default false,
  created_at      timestamptz not null default now()
);

-- one row per department the user works in; level 1..n from workflow.json's org (D9)
create table app.memberships (
  user_id    uuid not null references app.users (id) on delete cascade,
  department text not null,
  level      int not null check (level >= 1),
  scope      text not null default 'own' check (scope in ('own', 'department', 'all')),
  primary key (user_id, department)
);

-- rotating refresh tokens: one family per sign-in; a reused token revokes its whole family
create table app.refresh_tokens (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references app.users (id) on delete cascade,
  token_hash text not null unique,                    -- sha256 of the cookie value
  family     uuid not null,
  expires_at timestamptz not null,
  revoked_at timestamptz
);
create index on app.refresh_tokens (family);
