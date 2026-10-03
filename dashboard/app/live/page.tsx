import type { Metadata } from "next";
import Link from "next/link";
import { getServerSession } from "next-auth";

import { LiveView } from "@/components/LiveView";
import { authOptions } from "@/lib/live/auth";
import { isOwner } from "@/lib/live/owner";
import { INVESTIGATION } from "@/lib/live/validate";

// Rendered per request (it reads the session cookie). It shows no AWS data
// itself: the owner's browser fetches that from /api/live/status, which
// checks the session again.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Live | NightShift",
  robots: { index: false, follow: false },
};

type Props = { searchParams: Promise<{ investigation?: string }> };

export default async function LivePage({ searchParams }: Props) {
  const session = await getServerSession(authOptions);
  if (!session || !isOwner(session.githubId)) {
    return (
      <>
        <h1>Live</h1>
        <p className="lead">
          The live view of the store and the agent, and the buttons that
          approve or reject an action the agent proposed, are for the
          project&apos;s owner only. Everything public is under Results and
          Incidents, replayed from recorded runs.
        </p>
        {session ? (
          <p>
            This account is not allowed.{" "}
            <Link href="/api/auth/signout" prefetch={false}>
              Sign out
            </Link>
          </p>
        ) : (
          <p>
            <Link href="/api/auth/signin?callbackUrl=%2Flive" prefetch={false}>
              Sign in with GitHub
            </Link>
          </p>
        )}
      </>
    );
  }
  const { investigation } = await searchParams;
  const requested = investigation && INVESTIGATION.test(investigation) ? investigation : undefined;
  return <LiveView login={session.login ?? "owner"} investigation={requested} />;
}
