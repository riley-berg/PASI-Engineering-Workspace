import {copyFile, mkdir} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "node_modules/typescript/lib/typescript.js");
const target = resolve(root, "extensions/pasi-chatgpt/src/vendor/typescript.js");
await mkdir(dirname(target), {recursive: true});
await copyFile(source, target);
console.log("Built userscript TypeScript compiler:", target);
