"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Copy, Check } from "lucide-react";
import { useState } from "react";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

function CodeBlock({
  language,
  children,
}: {
  language: string | undefined;
  children: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative group my-3">
      <button
        onClick={handleCopy}
        className="absolute right-2 top-2 p-1.5 rounded-md bg-muted hover:bg-border opacity-0 group-hover:opacity-100 transition-opacity"
        title="Copy code"
      >
        {copied ? (
          <Check className="w-4 h-4 text-accent" />
        ) : (
          <Copy className="w-4 h-4 text-muted-foreground" />
        )}
      </button>
      <SyntaxHighlighter
        style={oneDark}
        language={language || "text"}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: "0.5rem",
          fontSize: "0.8rem",
          background: "var(--card)",
        }}
        codeTagProps={{
          style: {
            fontFamily: "var(--font-mono)",
          },
        }}
      >
        {children}
      </SyntaxHighlighter>
      {language && (
        <span className="absolute top-2 left-3 text-[10px] text-muted-foreground uppercase tracking-wider">
          {language}
        </span>
      )}
    </div>
  );
}

export default function MarkdownRenderer({
  content,
  className = "",
}: MarkdownRendererProps) {
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
        code({ node, inline, className, children, ...props }: any) {
          const match = /language-(\w+)/.exec(className || "");
          const codeContent = String(children).replace(/\n$/, "");

          if (!inline && (match || codeContent.includes("\n"))) {
            return (
              <CodeBlock language={match?.[1]}>{codeContent}</CodeBlock>
            );
          }

          return (
            <code
              className="px-1.5 py-0.5 rounded bg-muted text-foreground text-[0.85em] font-mono"
              {...props}
            >
              {children}
            </code>
          );
        },

        p({ children }) {
          return <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>;
        },

        h1({ children }) {
          return (
            <h1 className="text-xl font-bold mb-3 mt-4 first:mt-0 text-foreground">
              {children}
            </h1>
          );
        },
        h2({ children }) {
          return (
            <h2 className="text-lg font-semibold mb-2 mt-3 first:mt-0 text-foreground">
              {children}
            </h2>
          );
        },
        h3({ children }) {
          return (
            <h3 className="text-base font-semibold mb-2 mt-3 first:mt-0 text-foreground">
              {children}
            </h3>
          );
        },

        ul({ children }) {
          return (
            <ul className="list-disc list-inside mb-3 space-y-1 ml-2">
              {children}
            </ul>
          );
        },
        ol({ children }) {
          return (
            <ol className="list-decimal list-inside mb-3 space-y-1 ml-2">
              {children}
            </ol>
          );
        },
        li({ children }) {
          return <li className="leading-relaxed">{children}</li>;
        },

        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline underline-offset-2 hover:text-accent/80"
            >
              {children}
            </a>
          );
        },

        blockquote({ children }) {
          return (
            <blockquote className="border-l-2 border-border pl-4 my-3 text-muted-foreground italic">
              {children}
            </blockquote>
          );
        },

        table({ children }) {
          return (
            <div className="overflow-x-auto my-3">
              <table className="min-w-full border-collapse text-sm">
                {children}
              </table>
            </div>
          );
        },
        thead({ children }) {
          return <thead className="bg-muted">{children}</thead>;
        },
        th({ children }) {
          return (
            <th className="px-3 py-2 text-left border border-border font-semibold">
              {children}
            </th>
          );
        },
        td({ children }) {
          return (
            <td className="px-3 py-2 border border-border">{children}</td>
          );
        },

        hr() {
          return <hr className="my-4 border-border" />;
        },

        strong({ children }) {
          return <strong className="font-semibold text-foreground">{children}</strong>;
        },
        em({ children }) {
          return <em className="italic">{children}</em>;
        },

        img({ src, alt }) {
          return (
            <img
              src={src}
              alt={alt || ""}
              className="max-w-full h-auto rounded-lg my-3"
            />
          );
        },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
