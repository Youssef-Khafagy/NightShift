import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

// Postmortems quote tool output, and tool output is untrusted: scenario 13
// plants an instruction in an order note. skipHtml drops any raw HTML in the
// text instead of rendering it, and react-markdown's default URL check
// removes javascript: and similar links. Everything else is escaped text.
// remark-gfm adds tables, which every postmortem's answer section uses.
export function Markdown({ text }: { text: string }) {
  return (
    <div className={styles.md}>
      <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
