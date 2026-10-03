// The one GitHub account allowed into the live pages. A numeric ID, not a
// username: a username can be renamed and then registered by someone else,
// an ID never changes. It is public (it is in the repo's OIDC subject too).
export const OWNER_GITHUB_ID = "232406487";

export function isOwner(githubId: string | null | undefined): boolean {
  return githubId === OWNER_GITHUB_ID;
}
