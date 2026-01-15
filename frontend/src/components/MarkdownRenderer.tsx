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
        className="absolute right-2 top-2 p-1.5 rounded-md bg-white/10 hover:bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Copy code"
      >
        {copied ? (
          <Check className="w-4 h-4 text-green-400" />
        ) : (
          <Copy className="w-4 h-4 text-white/60" />
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
          background: "rgba(0, 0, 0, 0.4)",
        }}
        codeTagProps={{
          style: {
            fontFamily:
              'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace',
          },
        }}
      >
        {children}
      </SyntaxHighlighter>
      {language && (
        <span className="absolute top-2 left-3 text-[10px] text-white/40 uppercase tracking-wider">
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
        // Code blocks
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
              className="px-1.5 py-0.5 rounded bg-white/10 text-coral-300 text-[0.85em] font-mono"
              {...props}
            >
              {children}
            </code>
          );
        },

        // Paragraphs
        p({ children }) {
          return <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>;
        },

        // Headings
        h1({ children }) {
          return (
            <h1 className="text-xl font-bold mb-3 mt-4 first:mt-0 text-white">
              {children}
            </h1>
          );
        },
        h2({ children }) {
          return (
            <h2 className="text-lg font-semibold mb-2 mt-3 first:mt-0 text-white">
              {children}
            </h2>
          );
        },
        h3({ children }) {
          return (
            <h3 className="text-base font-semibold mb-2 mt-3 first:mt-0 text-white">
              {children}
            </h3>
          );
        },

        // Lists
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

        // Links
        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ocean-400 hover:text-ocean-300 underline underline-offset-2"
            >
              {children}
            </a>
          );
        },

        // Blockquotes
        blockquote({ children }) {
          return (
            <blockquote className="border-l-2 border-ocean-500/50 pl-4 my-3 text-white/70 italic">
              {children}
            </blockquote>
          );
        },

        // Tables
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
          return <thead className="bg-white/10">{children}</thead>;
        },
        th({ children }) {
          return (
            <th className="px-3 py-2 text-left border border-white/10 font-semibold">
              {children}
            </th>
          );
        },
        td({ children }) {
          return (
            <td className="px-3 py-2 border border-white/10">{children}</td>
          );
        },

        // Horizontal rule
        hr() {
          return <hr className="my-4 border-white/10" />;
        },

        // Strong and emphasis
        strong({ children }) {
          return <strong className="font-semibold text-white">{children}</strong>;
        },
        em({ children }) {
          return <em className="italic">{children}</em>;
        },

        // Images
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
