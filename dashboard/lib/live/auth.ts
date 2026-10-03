// Sign-in for the live pages: GitHub OAuth through next-auth (Auth.js's
// stable release), allowed for exactly one GitHub account.
//
// The session is a signed, encrypted cookie (JWT strategy), so there is no
// database. It lasts 8 hours, not next-auth's default 30 days. The GitHub
// scope is read:user, the least GitHub offers: it reads the public profile
// and nothing else, not even the email address.

import type { NextAuthOptions } from "next-auth";
import GitHubProvider from "next-auth/providers/github";

import { isOwner } from "./owner";

export const authOptions: NextAuthOptions = {
  providers: [
    GitHubProvider({
      clientId: process.env.GITHUB_ID ?? "",
      clientSecret: process.env.GITHUB_SECRET ?? "",
      authorization: { params: { scope: "read:user" } },
    }),
  ],
  session: { strategy: "jwt", maxAge: 8 * 60 * 60 },
  callbacks: {
    // Refuse every account but the owner's before a session exists at all.
    signIn({ account }) {
      return account?.provider === "github" && isOwner(account.providerAccountId);
    },
    jwt({ token, account, profile }) {
      if (account) {
        token.githubId = account.providerAccountId;
        token.login = (profile as { login?: string } | undefined)?.login;
      }
      return token;
    },
    session({ session, token }) {
      session.githubId = token.githubId;
      session.login = token.login;
      return session;
    },
  },
};
