import type { ZudokuConfig } from "zudoku";
import fixPagefindUrlsPlugin from "./plugins/fix-pagefind-urls";

// Get docs URL from environment variable (defaults to localhost for dev)
const docsUrl = process.env.DOCS_URL || "http://localhost:3000";

const config: ZudokuConfig = {
  site: {
    logo: {
      src: { light: "/moca-logo-light.svg", dark: "/moca-logo-dark.svg" },
      alt: "MOCA Library",
      width: "130px",
    },
  },
  theme: {
    light: {
      primary: "#2563eb",
      accent: "#f0f9ff",
    },
    dark: {
      primary: "#3b82f6",
      accent: "#0c1b2e",
    },
  },
  // Enable search with Pagefind
  search: {
    type: "pagefind",
    maxSubResults: 3,
    ranking: {
      termFrequency: 0.8,
      pageLength: 0.6,
      termSimilarity: 1.2,
      termSaturation: 1.2,
    },
    transformResults: ({ result }) => {
      // Remove .html extension from URLs to match clean URL structure
      if (!result) return result;

      const modifiedResult = { ...result };

      // Fix main result URL
      if (modifiedResult.url && typeof modifiedResult.url === "string" && modifiedResult.url.includes(".html")) {
        modifiedResult.url = modifiedResult.url.replace(/\.html(?=[#?]|$)/, "");
      }

      // Fix sub-results URLs if they exist
      if (modifiedResult.sub_results && Array.isArray(modifiedResult.sub_results)) {
        modifiedResult.sub_results = modifiedResult.sub_results.map((subResult) => {
          if (subResult && subResult.url && typeof subResult.url === "string" && subResult.url.includes(".html")) {
            return {
              ...subResult,
              url: subResult.url.replace(/\.html(?=[#?]|$)/, ""),
            };
          }
          return subResult;
        });
      }

      return modifiedResult;
    },
  },
  // Configure docs with LLM-friendly output
  docs: {
    files: "pages/**/*.{md,mdx}",
    publishMarkdown: true,
    llms: {
      llmsTxt: true,
      llmsTxtFull: true,
      includeProtected: false,
    },
  },
  navigation: [
    {
      type: "category",
      label: "Documentation",
      items: [
        {
          type: "category",
          label: "Getting Started",
          icon: "sparkles",
          items: [
            "/introduction",
            "/quickstart",
            "/configuration",
          ],
        },
        {
          type: "category",
          label: "Core Features",
          icon: "box",
          items: [
            "/features/document-upload",
            "/features/search",
            "/features/ask-ai",
            "/features/knowledge-graph",
            "/features/collections",
            "/features/communities",
            "/features/skills",
            "/features/turbo-mode",
          ],
        },
        {
          type: "category",
          label: "Guides",
          icon: "book-open",
          items: [
            "/guides/deployment",
            "/guides/authentication",
            "/guides/security",
            "/guides/data-transfer",
          ],
        },
        {
          type: "category",
          label: "Examples",
          icon: "code",
          items: [
            "/examples/python",
            "/examples/curl",
            "/examples/integration",
          ],
        },
        {
          type: "category",
          label: "Resources",
          collapsible: false,
          icon: "link",
          items: [
            {
              type: "link",
              icon: "github",
              label: "GitHub",
              to: "https://github.com/mocaOS/library",
            },
            {
              type: "link",
              icon: "book",
              label: "mdharvest Scraper",
              to: "https://github.com/mocaOS/mdharvest",
            },
            {
              type: "link",
              icon: "file-text",
              label: "llms.txt",
              to: `${docsUrl}/llms.txt`,
              target: "_blank",
            },
            {
              type: "link",
              icon: "file-text",
              label: "llms-full.txt",
              to: `${docsUrl}/llms-full.txt`,
              target: "_blank",
            },
          ],
        },
      ],
    },
    {
      type: "category",
      label: "Changelog",
      icon: "list",
      items: ["/changelog"],
    },
    {
      type: "link",
      to: "/api",
      label: "API Reference",
    },
  ],
  redirects: [{ from: "/", to: "/introduction" }],
  plugins: [fixPagefindUrlsPlugin],
  apis: [
    {
      type: "file",
      input: "./apis/openapi.yaml",
      path: "/api",
    },
  ],
};

export default config;
