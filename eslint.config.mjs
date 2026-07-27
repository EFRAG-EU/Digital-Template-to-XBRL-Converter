import js from "@eslint/js";
import globals from "globals";
import { defineConfig } from "eslint/config";

// Processor that extracts the JavaScript from inline <script> blocks in the
// webapp's Jinja templates so eslint can lint it. Everything outside an inline
// <script> (HTML, {% ... %} / {{ ... }} Jinja) is replaced with blanks of the
// same length, and Jinja expressions *inside* a script are likewise blanked.
// Because blanking preserves every byte offset and newline, reported line and
// column numbers — and --fix ranges — map 1:1 back onto the original file, so
// no message re-mapping is needed in postprocess.
//
// Scope/limit: this handles scripts that are JavaScript with, at most, Jinja
// interpolation. It does not try to reason about Jinja control flow ({% for %},
// {% if %}) that would emit or remove JS statements — none of the templates do
// that today.
const blank = (s) => s.replace(/[^\n]/g, " ");

const jinjaTemplateProcessor = {
  meta: { name: "jinja-inline-script", version: "1.0.0" },
  supportsAutofix: true,
  preprocess(text, filename) {
    const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let out = "";
    let lastIndex = 0;
    let hasScript = false;
    let match;
    while ((match = scriptRe.exec(text)) !== null) {
      const [full, attrs] = match;
      const start = match.index;
      const bodyStart = start + full.indexOf(">") + 1;
      const bodyEnd = start + full.lastIndexOf("</script>");

      // Blank everything up to and including this opening <script ...> tag.
      out += blank(text.slice(lastIndex, bodyStart));

      const isExternal = /\bsrc\s*=/i.test(attrs);
      const type = attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i);
      const isJs =
        !type || /^(module|(text|application)\/javascript)$/i.test(type[1]);

      if (isExternal || !isJs) {
        out += blank(text.slice(bodyStart, bodyEnd));
      } else {
        // Keep the JS verbatim, but blank Jinja {{ ... }} / {% ... %} so they
        // don't trip the JS parser (length preserved to keep offsets aligned).
        out += text
          .slice(bodyStart, bodyEnd)
          .replace(/\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g, blank);
        hasScript = true;
      }
      lastIndex = bodyEnd;
    }
    if (!hasScript) return [];
    out += blank(text.slice(lastIndex));
    return [{ text: out, filename: `${filename}.js` }];
  },
  postprocess(messagesPerBlock) {
    return messagesPerBlock.flat();
  },
};

export default defineConfig([
  { ignores: [".venv-*/**", "htmlcov/**", "**/__pycache__/**"] },
  // Lint the JS embedded in the webapp's Jinja templates. The processor emits
  // a virtual *.js file, so the JS config block below applies to it too.
  {
    files: ["**/*.html.jinja"],
    processor: jinjaTemplateProcessor,
  },
  {
    files: ["**/*.{js,mjs,cjs}"],
    plugins: { js },
    extends: ["js/recommended"],
    languageOptions: { globals: globals.browser },
  },
]);
