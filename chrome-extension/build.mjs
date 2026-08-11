import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");

const common = {
  bundle: true,
  format: "iife",
  minify: !watch,
  sourcemap: watch,
  target: "chrome120",
};

const configs = [
  { ...common, entryPoints: ["src/popup.js"], outfile: "dist/popup.js" },
  { ...common, entryPoints: ["src/content.js"], outfile: "dist/content.js" },
  { ...common, entryPoints: ["src/options.js"], outfile: "dist/options.js" },
  { ...common, entryPoints: ["src/background.js"], outfile: "dist/background.js" },
];

if (watch) {
  for (const cfg of configs) {
    const ctx = await esbuild.context(cfg);
    await ctx.watch();
  }
  console.log("Watching for changes...");
} else {
  for (const cfg of configs) {
    await esbuild.build(cfg);
  }
  console.log("Build complete");
}
