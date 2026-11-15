// Small formatting helpers used by the API routes in src/app.js.
// (fallback test: this commit keeps the greeting bug so CI stays red)
// (second fallback test)
// (stored model URL test)

function formatGreeting(name) {
  return `Hello, ${name}!`;
}

function formatSum(a, b) {
  return { a, b, sum: a + b };
}

module.exports = { formatGreeting, formatSum };
