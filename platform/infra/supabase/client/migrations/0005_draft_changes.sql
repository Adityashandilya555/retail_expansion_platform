-- 0005: the draft's change log (#33). Every edit of app.drafts is one row here: the RFC 6902 patch
-- applied, its inverse (what undo applies), who made it and the sha256 of the draft it left (undo
-- refuses when the draft no longer matches, e.g. after a reseed). zm_api may only read and insert:
-- 0001's default privileges are taken back for update and delete, and the trigger below refuses
-- them for everyone else.

alter table app.drafts add column base_version int;  -- the release the draft was published as

create table app.draft_changes (
  id                bigserial primary key,
  at                timestamptz not null default now(),
  author_owner_id   uuid not null,                    -- platform owner (platform.owners.id)
  conversation_id   uuid,                             -- the agent conversation, if any (#36)
  change_request_id uuid,                             -- the change request it ships, if any
  tool              text not null,
  patch             jsonb not null,
  inverse           jsonb not null,
  summary           text not null,
  undoes            bigint unique references app.draft_changes (id), -- set on an undo's row
  after_sha256      text not null                     -- of app.drafts.workflow::text after it
);
revoke update, delete on app.draft_changes from zm_api;

create function app.draft_changes_insert_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'app.draft_changes is insert-only';
end $$;

create trigger draft_changes_insert_only before update or delete on app.draft_changes
  for each row execute function app.draft_changes_insert_only();
