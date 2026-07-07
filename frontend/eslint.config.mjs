import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = [
  ...nextCoreWebVitals,
  {
    rules: {
      // New in eslint-plugin-react-hooks v6 (via eslint-config-next 16).
      // The flagged spots are intentional sync-from-props/action patterns;
      // keep as a warning until each is refactored deliberately.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default eslintConfig;
