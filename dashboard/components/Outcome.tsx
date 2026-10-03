import { outcome } from "@/lib/format";

// Right, hedged or wrong, as an icon and a word (the colour only repeats it).
export function Outcome({ correct, hedged }: { correct: boolean; hedged: boolean }) {
  const o = outcome({ correct, hedged });
  return <span className={`outcome outcome-${o}`}>{o}</span>;
}
