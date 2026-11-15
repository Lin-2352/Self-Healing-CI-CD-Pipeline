// The demo API. test/api.test.js boots it and checks every route.
const express = require("express");
const { formatGreeting, formatSum } = require("./format");

const app = express();

app.get("/health", (req, res) => res.json({ status: "ok" }));

app.get("/greet/:name", (req, res) => {
  res.json({ message: formatGreeting(req.params.name) });
});

app.get("/sum", (req, res) => {
  const a = Number(req.query.a);
  const b = Number(req.query.b);
  if (Number.isNaN(a) || Number.isNaN(b)) {
    return res.status(400).json({ error: "a and b must be numbers" });
  }
  res.json(formatSum(a, b));
});

module.exports = app;
