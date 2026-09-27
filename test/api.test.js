// API test: boots src/app.js and checks each route's status and body.
// Same exit-code contract as test.js:
//   0 - all good
//   1 - CODE_FAILURE  : a route is broken; worth sending to the AI to fix
//   75 - INFRA_FAILURE : port taken etc. Nothing to fix in the source.
const http = require("http");
const app = require("../src/app");

const PORT = Number(process.env.API_PORT) || 5055;

function get(path) {
  return new Promise((resolve, reject) => {
    http
      .get(`http://localhost:${PORT}${path}`, (res) => {
        let body = "";
        res.on("data", (c) => (body += c));
        res.on("end", () => resolve({ status: res.statusCode, body }));
      })
      .on("error", reject);
  });
}

const checks = [
  ["/health", 200, { status: "ok" }],
  ["/greet/Ada", 200, { message: "Hello, Ada!" }],
  ["/sum?a=2&b=3", 200, { a: 2, b: 3, sum: 5 }],
  ["/sum?a=x&b=3", 400, { error: "a and b must be numbers" }],
];

const server = app.listen(PORT, async () => {
  try {
    for (const [path, status, body] of checks) {
      const res = await get(path);
      const got = JSON.parse(res.body || "null");
      if (res.status !== status || JSON.stringify(got) !== JSON.stringify(body)) {
        console.error(`CODE_FAILURE: GET ${path} -> ${res.status} ${res.body} (expected ${status} ${JSON.stringify(body)})`);
        server.close(() => process.exit(1));
        return;
      }
    }
    console.log(`api test passed (${checks.length} routes)`);
    server.close(() => process.exit(0));
  } catch (err) {
    console.error(`CODE_FAILURE: ${err.stack || err.message}`);
    server.close(() => process.exit(1));
  }
});

server.on("error", (err) => {
  console.error(`INFRA_FAILURE: ${err.code || "unknown"}: ${err.message} - environment problem, not a code bug`);
  process.exit(75);
});
