-- 0007: each user's appearance (#93, D19): {color_scheme, accent}, only colours. Written by
-- PUT /app/me/preferences, read with GET /app/me; a missing key means "no choice yet".
alter table app.users add column preferences jsonb not null default '{}';
