// Small formatting helpers used by the API routes in src/app.js.
// (fallback test: this commit keeps the greeting bug so CI stays red)
// (second fallback test)

function formatGreeting(name) {
  return `Hello, ${clean}!`;
}

function formatSum(a, b) {
  return { a, b, sum: a + b };
}

module.exports = { formatGreeting, formatSum };
