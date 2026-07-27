// Tiny string utilities with deliberately mixed-truth documentation.

export function add(a, b) {
  return a + b;
}

export function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
