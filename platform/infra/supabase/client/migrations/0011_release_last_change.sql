-- 0011: a release records the last draft change it carries (#46): app.draft_changes.id of the
-- newest row when it was published, read with the draft row locked, so it is exact. A change
-- request ships in the first release whose last_change_id covers its newest edit still in the
-- draft, whichever came first, the approval or the publish. Null on releases published before
-- this (no change request had edits then).
alter table app.releases add column last_change_id bigint;
