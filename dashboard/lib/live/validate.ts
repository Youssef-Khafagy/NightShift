// What a live request may name. Investigation IDs are 12 hex characters;
// labelled test records use short lowercase names. Approval items are
// approval#1, approval#2 and so on; hashes are SHA-256 in hex.
export const INVESTIGATION = /^[a-z0-9-]{1,40}$/;
export const APPROVAL_ITEM = /^approval#[0-9]{1,2}$/;
export const HASH = /^[0-9a-f]{64}$/;
