// Smoke test used by the CI pipeline.
// Boots the Express server, calls itself over HTTP, and exits non-zero on any
// failure. A non-zero exit is what triggers the self-healing workflow.
//
// Exit codes are deliberately distinct so the pipeline can tell a real code bug
// from an environment problem:
//   0  - healthy
//   1  - CODE_FAILURE      : the app is broken; worth sending to the AI to fix
//   75 - INFRA_FAILURE     : port taken, DNS, etc. Nothing to fix in the source.
//        (75 = EX_TEMPFAIL, the conventional "try again later" code)
const express = require("express");

const PORT = Number(process.env.PORT) || 5000;

const app = express();

app.get("/", (req, res) => res.send("ok"));

function infraFail(err) {
  const hint =
    err.code === "EADDRINUSE"
      ? `port ${PORT} is already in use`
      : `${err.code || "unknown"}: ${err.message}`;
  console.error(`INFRA_FAILURE: ${hint} - environment problem, not a code bug`);
  process.exit(75);
}

const server = app.listen(PORT, () => {
  http
    .get(`http://localhost:${PORT}`, (res) => {
      if (res.statusCode === 200) {
        console.log("smoke test passed");
        server.close(() => process.exit(0));
      } else {
        console.error(`CODE_FAILURE: unexpected status ${res.statusCode}`);
        server.close(() => process.exit(1));
      }
    })
    .on("error", (err) => {
      // The server is listening but will not answer itself - that is the app's fault.
      console.error(`CODE_FAILURE: ${err.message}`);
      server.close(() => process.exit(1));
    });
});

// Fires when the listen() itself fails (EADDRINUSE, EACCES, ...). Without this
// handler Node throws an unhandled 'error' event and the pipeline would hand the
// AI a stack trace for a problem that is not in the source at all.
server.on("error", infraFail);
