// Build do módulo "Controle de Orçamento" → static/controle-orcamento/
// Uso: npm run build   (ou npm run watch para desenvolvimento)
import { build, context } from "esbuild";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, "../../static/controle-orcamento-exec");
const watch = process.argv.includes("--watch");

const jsOptions = {
  entryPoints: [path.join(here, "src/main.jsx")],
  bundle: true,
  minify: true,
  sourcemap: false,
  target: ["es2018"],
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  outfile: path.join(outDir, "app.js"),
  legalComments: "none",
  logLevel: "info",
};

function buildCss() {
  const bin = path.join(here, "node_modules/.bin/tailwindcss");
  execSync(
    `"${bin}" -c "${path.join(here, "tailwind.config.js")}" -i "${path.join(here, "src/index.css")}" -o "${path.join(outDir, "app.css")}" --minify`,
    { stdio: "inherit" }
  );
}

if (watch) {
  const ctx = await context(jsOptions);
  await ctx.watch();
  buildCss();
  console.log("watching…");
} else {
  await build(jsOptions);
  buildCss();
  console.log("build ok →", outDir);
}
