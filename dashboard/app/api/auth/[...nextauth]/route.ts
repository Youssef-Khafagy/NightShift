// next-auth's own routes: sign in with GitHub, the OAuth callback, sign out.
// The signIn callback in lib/live/auth.ts refuses every account but the
// owner's. These routes never touch AWS.
import NextAuth from "next-auth";

import { authOptions } from "@/lib/live/auth";

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
