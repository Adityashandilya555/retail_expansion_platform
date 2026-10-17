-- 0001: two schemas per client vault. Passwords are set by the applier, never here.

-- 1. The two login users. Created only if missing, so the file also works where they exist.
do $$
begin
  if not exists (select from pg_roles where rolname = 'zm_engine') then
    create role zm_engine login;
  end if;
  if not exists (select from pg_roles where rolname = 'zm_api') then
    create role zm_api login;
  end if;
end $$;

-- the migration user must be a member of zm_engine to hand it the operaton schema and set its defaults
grant zm_engine to current_user;

-- 2. The two rooms
create schema if not exists app;
create schema if not exists operaton;
alter schema operaton owner to zm_engine;          -- also fixes the copy the bootstrap created
alter role zm_engine set search_path = operaton;

-- 3. Lock out strangers, now and for future tables
revoke all on schema public, app, operaton from public, anon, authenticated;
revoke all on all tables in schema public, app, operaton from public, anon, authenticated;
alter default privileges in schema public, app revoke all on tables from public, anon, authenticated;
alter default privileges for role zm_engine in schema operaton revoke all on tables from public, anon, authenticated;

-- 4. zm_api: read + write in app
grant usage on schema app to zm_api;
grant select, insert, update, delete on all tables in schema app to zm_api;
grant usage, select on all sequences in schema app to zm_api;
alter default privileges in schema app grant select, insert, update, delete on tables to zm_api;
alter default privileges in schema app grant usage, select on sequences to zm_api;
-- the applier's own bookkeeping (created before this file runs) stays out of the API's reach
revoke all on app.schema_migrations from zm_api;

-- 5. zm_api: read-only in operaton (Operaton creates its tables later, as zm_engine)
grant usage on schema operaton to zm_api;
alter default privileges for role zm_engine in schema operaton grant select on tables to zm_api;