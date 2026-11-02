import { dirname } from "path";
import { fileURLToPath } from "url";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
void __dirname;

const eslintConfig = [...nextVitals, ...nextTs, { ignores: [".next/**", "node_modules/**"] }];
export default eslintConfig;
