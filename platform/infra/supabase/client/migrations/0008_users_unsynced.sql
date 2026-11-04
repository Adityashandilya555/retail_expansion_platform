-- 0008: a people change Operaton hasn't got yet (S2-23). PATCH /app/admin/users/{id} sets it with
-- the change; mirroring the user clears it, and the worker's reconciler retries the users it marks.
alter table app.users add column unsynced_since timestamptz;
