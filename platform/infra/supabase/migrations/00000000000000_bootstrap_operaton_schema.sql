-- Bootstrap for the local pilot vault (S1-01, #9): Operaton connects with ?currentSchema=operaton
-- and creates its own tables there, so the schema must exist before operaton-pilot starts.
-- S1-03b (#12) owns the client vault migrations (app + operaton schemas, roles, grants) and may supersede this file.
create schema if not exists operaton;
