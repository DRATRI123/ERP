import next from "eslint-config-next";

const config = [
  ...next,
  { ignores: [".next/**", "node_modules/**", "e2e/**", "next-env.d.ts"] },
];

export default config;
