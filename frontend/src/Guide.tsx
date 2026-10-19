import { useEffect, useState, type ReactNode } from "react";
import { api, savedToken, type Onboarding, type User } from "./api";

/**
 * The vendor guide (docs/VENDOR-GUIDE.md), with the reader's own values in place of its
 * placeholders. One source for the repo and the app, so the two never drift apart.
 */
export default function Guide({ me }: { me: User }) {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const state = await api<Onboarding>(`/orgs/${me.org_id}/onboarding`);
      const r = await fetch("/api/guide/vendor", { headers: { Authorization: `Bearer ${savedToken()}` } });
      const values: Record<string, string> = {
        "<vendor>": state.org_name,
        "<namespace>": state.namespace,
        "<registry>": state.registry_host ?? "localhost:5000",
        "<api>": `${window.location.protocol}//${window.location.hostname}:8000`,
      };
      // The note about placeholders is for readers of the repo; here they're already filled in.
      const guide = (await r.text()).replace(/<!-- repo -->[\s\S]*?<!-- \/repo -->\n?/g, "");
      setText(Object.entries(values).reduce((t, [k, v]) => t.replaceAll(k, v), guide));
    })();
  }, [me.org_id]);

  return <article className="guide-doc">{text ? markdown(text) : null}</article>;
}

/** Inline code, bold and links (shown as their text), which is all the guide uses. */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("[")) return part.slice(1, part.indexOf("]"));
    return part;
  });
}

/** The Markdown the guide is written in: headings, paragraphs, lists, tables and code blocks. */
function markdown(text: string): ReactNode[] {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  for (let i = 0; i < lines.length; ) {
    const line = lines[i];
    const key = out.length;
    if (line.startsWith("```")) {
      const end = lines.indexOf("```", i + 1);
      out.push(<pre key={key} className="commands">{lines.slice(i + 1, end).join("\n")}</pre>);
      i = end + 1;
    } else if (line.startsWith("#")) {
      const level = line.indexOf(" ");
      const content = inline(line.slice(level + 1));
      out.push(level === 1 ? <h2 key={key}>{content}</h2> : level === 2 ? <h3 key={key}>{content}</h3> : <h4 key={key}>{content}</h4>);
      i++;
    } else if (line.startsWith("|")) {
      const rows: string[][] = [];
      for (; i < lines.length && lines[i].startsWith("|"); i++) rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
      out.push(
        <table key={key}>
          <thead><tr>{rows[0].map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
          <tbody>{rows.slice(2).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>,
      );
    } else if (/^(- |\d+\. )/.test(line)) {
      const items: string[] = [];
      for (; i < lines.length && /^(- |\d+\. )/.test(lines[i]); i++) items.push(lines[i].replace(/^(- |\d+\. )/, ""));
      const list = items.map((it, j) => <li key={j}>{inline(it)}</li>);
      out.push(line.startsWith("- ") ? <ul key={key}>{list}</ul> : <ol key={key}>{list}</ol>);
    } else if (line.trim()) {
      const para: string[] = [];
      for (; i < lines.length && lines[i].trim() && !/^(#|\||```|- |\d+\. )/.test(lines[i]); i++) para.push(lines[i]);
      out.push(<p key={key}>{inline(para.join(" "))}</p>);
    } else {
      i++;
    }
  }
  return out;
}
