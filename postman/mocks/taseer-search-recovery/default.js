const http = require("http");
const PORT = process.env.PORT || 4500;

const server = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/v1/search/stream") {
    // Model a proxy that closes a successful stream before it sends a `done` event.
    res.writeHead(200, { "Content-Type": "application/x-ndjson" });
    return res.end("");
  }

  if (req.method === "POST" && req.url === "/v1/search") {
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({
      trace_id: "fallback-trace",
      state: "DONE",
      results: [{ ad: { id: "recovered-ad" } }],
      groups: [],
      next_page: null,
    }));
  }

  res.writeHead(501, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "unmodeled request" }));
});

server.listen(PORT);
