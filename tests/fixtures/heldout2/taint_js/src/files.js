const fs = require("fs");
const path = require("path");
const BASE = "/srv/files";

function serve(req, res) {
  const name = req.query.file;
  const full = path.join(BASE, name);
  fs.readFile(full, (err, data) => res.send(data));
}

function serveFixed(res) {
  const full = path.join(BASE, "readme.txt");
  fs.readFile(full, (err, data) => res.send(data));
}

module.exports = { serve, serveFixed };
