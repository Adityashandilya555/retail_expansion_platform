-- Local isolation client vault (qsr-local): mimic a Supabase project so the client
-- migrations from S1-03b (#12) apply unchanged. Runs once, on an empty data volume.
create role anon nologin;
create role authenticated nologin;
create schema if not exists operaton;
