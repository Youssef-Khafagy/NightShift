import type { Metadata } from "next";
import Link from "next/link";

import { loadIndex } from "@/lib/replay";
import "./globals.css";
import styles from "./layout.module.css";

export const metadata: Metadata = {
  title: "NightShift",
  description:
    "An AI on-call engineer for AWS, measured against baselines on 36 staged incidents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const index = loadIndex();
  return (
    <html lang="en">
      <body>
        <header className={styles.header}>
          <div className={`wrap ${styles.bar}`}>
            <Link href="/" className={styles.brand}>
              NightShift
            </Link>
            <nav className={styles.nav} aria-label="Main">
              <Link href="/demo">Demo</Link>
              <Link href="/">Results</Link>
              <Link href="/incidents">Incidents</Link>
              <Link href="/method">Method</Link>
              {/* Owner only. No prefetch: it renders per request, and a
                  visitor's browser should not run it just by loading a page. */}
              <Link href="/live" prefetch={false}>
                Live
              </Link>
            </nav>
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className={`wrap ${styles.footer}`}>
          Replay of benchmark pass <code>{index.pass}</code> at commit{" "}
          <code>{index.commits.join(", ")}</code>. Every page is built from the
          recorded results; none of them can call AWS or a model.
        </footer>
      </body>
    </html>
  );
}
