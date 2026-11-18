const axios = require("axios");

async function proxy(req, res) {
  const r = await axios.get(req.query.target);
  res.send(r.data);
}

async function catalog(res) {
  const r = await axios.get("https://api.example.com/catalog");
  res.send(r.data);
}

module.exports = { proxy, catalog };
