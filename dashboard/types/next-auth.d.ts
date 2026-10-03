// What our callbacks add to the session and the signed token
// (lib/live/auth.ts): the GitHub account's numeric ID and login.
import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    githubId?: string;
    login?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    githubId?: string;
    login?: string;
  }
}
