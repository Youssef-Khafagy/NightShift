import { pValue, verdict } from "@/lib/format";
import type { Comparison, Config } from "@/lib/types";

// Every configuration answered the same incidents, so two of them are
// compared pair by pair: only the incidents exactly one of them got right
// carry information (McNemar's exact test).
export function Comparisons({
  comparisons,
  configs,
}: {
  comparisons: Comparison[];
  configs: Config[];
}) {
  const label = Object.fromEntries(configs.map((c) => [c.key, c.label]));
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Question</th>
            <th scope="col">First</th>
            <th scope="col">Second</th>
            <th scope="col" className="num">
              Only first right
            </th>
            <th scope="col" className="num">
              Only second right
            </th>
            <th scope="col" className="num">
              p
            </th>
            <th scope="col">Reading</th>
          </tr>
        </thead>
        <tbody>
          {comparisons.map((c) => (
            <tr key={`${c.first}-${c.second}`}>
              <td>{c.question}</td>
              <td>{label[c.first]}</td>
              <td>{label[c.second]}</td>
              <td className="num">{c.only_first}</td>
              <td className="num">{c.only_second}</td>
              <td className="num">{pValue(c.p)}</td>
              <td>{verdict(c.p)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
